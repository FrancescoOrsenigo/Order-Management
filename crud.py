from sqlalchemy.orm import Session
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from models import Product, Order, OrderProduct
from schemas import OrderBase, ProductBase
from typing import Optional
from datetime import datetime
import redis
import time
import meilisearch
import os

# Redis setup
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
redis_client = redis.StrictRedis(host='redis', port=REDIS_PORT, db=0, decode_responses=True)

# MeiliSearch setup
MEILISEARCH_URL = os.getenv("MEILISEARCH_URL", "http://meilisearch:7700")
MEILISEARCH_API_KEY = os.getenv("MEILISEARCH_API_KEY", "masterKey")
meilisearch_client = meilisearch.Client(MEILISEARCH_URL, MEILISEARCH_API_KEY)
orders_index = meilisearch_client.index("orders")
# set only this fields as searchable
orders_index.update_searchable_attributes([
    "id",
    "name",  
    "description", 
    "created_at"  
])

orders_index.update_filterable_attributes(['created_at'])


def crud_create_order(db: Session, order: OrderBase) -> OrderBase:
    total_amount = 0.0
    product_updates = []

    for item in order.products:
        lock_key = f"lock_product_{item.product_id}"
        while not redis_client.setnx(lock_key, 1):  # Acquire lock
            time.sleep(0.1)  # Wait and retry
        redis_client.expire(lock_key, 5)  # Set expiration in case of crash

        try:
            product = db.query(Product).filter(Product.id == item.product_id).with_for_update().first()
            if not product:
                raise ValueError(f"Product with ID {item.product_id} not found")
            if product.stock < item.quantity:
                raise ValueError(f"Not enough stock for product {product.id}")

            product_updates.append((product, item.quantity))
            total_amount += product.price * item.quantity
        finally:
            redis_client.delete(lock_key)  # Release lock

    new_order = Order(name=order.name, description=order.description, total_amount=total_amount)
    db.add(new_order)
    db.commit()
    db.refresh(new_order)

    for product, quantity in product_updates:
        product.stock -= quantity
        order_product = OrderProduct(order_id=new_order.id, product_id=product.id, quantity=quantity)
        db.add(order_product)

    db.commit()
    db.refresh(new_order)
    # indexes the order
    index_order_in_meilisearch(new_order)
    
    return new_order


def crud_update_order(db: Session, order_id: int, order_data: OrderBase) -> OrderBase:
    """ updates all the fields of an order """
    # gets the existing order
    existing_order = db.query(Order).filter(Order.id == order_id).first()
    if not existing_order:
        raise HTTPException(status_code=404, detail="Order not found")

    # maps products of the old order
    existing_products_map = {op.product_id: op.quantity for op in existing_order.products}

    # maps products of the new order
    new_products_map = {item.product_id: item.quantity for item in order_data.products}

    stock_changes = {}

    # loops over the existing product list and checks if the product is still present and if the quantity changed
    for product_id, old_quantity in existing_products_map.items():
        # set quantity to 0 if the product is not present anymore
        new_quantity = new_products_map.get(product_id, 0) 
        # updates the quantity
        if new_quantity < old_quantity:
            stock_changes[product_id] = old_quantity - new_quantity
        elif new_quantity > old_quantity:
            stock_changes[product_id] = new_quantity - old_quantity

    # adds the new products to the list of products that need their stock updated
    for product_id, new_quantity in new_products_map.items():
        if product_id not in existing_products_map:
            stock_changes[product_id] = new_quantity

    # validates the order change, if the product exists and its stock support this
    for product_id, change in stock_changes.items():
        lock_key = f"lock_product_{product_id}"
        while not redis_client.setnx(lock_key, 1):
            time.sleep(0.1)
        redis_client.expire(lock_key, 5)

        try:
            product = db.query(Product).filter(Product.id == product_id).with_for_update().first()
            if not product:
                raise HTTPException(status_code=404, detail=f"Product with ID {product_id} not found")

            if change > 0 and product.stock < change:
                raise HTTPException(status_code=400, detail=f"Not enough stock for product {product.id}")

        finally:
            redis_client.delete(lock_key)  # Release lock

    # only apply the stock changes if all products are correct and they have enough stock
    for product_id, change in stock_changes.items():
        product = db.query(Product).filter(Product.id == product_id).first()
        if product_id in existing_products_map and new_products_map.get(product_id, 0) < existing_products_map[product_id]:
            # restock products
            product.stock += change
        else:
            # remove products from stock
            product.stock -= change

    # remove old order-products relationships
    db.query(OrderProduct).filter(OrderProduct.order_id == order_id).delete()

    # recalculate total amount and save the relationship between order and products in the db
    total_amount = 0.0
    for item in order_data.products:
        product = db.query(Product).filter(Product.id == item.product_id).first()
        total_amount += product.price * item.quantity
        db.add(OrderProduct(order_id=order_id, product_id=item.product_id, quantity=item.quantity))

    # update order details
    existing_order.name = order_data.name
    existing_order.description = order_data.description
    existing_order.total_amount = total_amount

    updated_order = existing_order
    db.commit()
    db.refresh(updated_order)

    # updates MeiliSearch index
    index_order_in_meilisearch(updated_order)

    return existing_order


def crud_delete_order(db: Session, order_id: int) -> OrderBase:

    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    # i restock the products not needed anymore for this order

    for order_product in order.products:
        lock_key = f"lock_product_{order_product.product_id}"
        
        while not redis_client.setnx(lock_key, 1):
            time.sleep(0.1)
        redis_client.expire(lock_key, 5)

        try:
            product = db.query(Product).filter(Product.id == order_product.product_id).with_for_update().first()
            # if the product is not found i got nothing to update
            if product:
                product.stock += order_product.quantity  # Restore stock
        finally:
            redis_client.delete(lock_key)

    # deletes the order-products relationships
    db.query(OrderProduct).filter(OrderProduct.order_id == order_id).delete()

    # deletes the order itself
    db.delete(order)
    db.commit()

    # removes order from Meilisearch
    orders_index.delete_document(str(order_id))    

    return order


def crud_get_order_by_id(db: Session, order_id: int) -> dict:
    """ gets a specific order """
    
    try:
        order = orders_index.get_document(str(order_id))
    except:
        raise HTTPException(status_code=404, detail="Order not found")

    order_dict = {
        "id": order.id,
        "name": order.name,
        "description": order.description,
        "created_at": str(datetime.fromtimestamp(order.created_at)),
        "total_amount": order.total_amount,
        "products": order.products
    }
    return JSONResponse(content=order_dict)



def crud_get_order_list(
        db: Session,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        search: Optional[str] = None
    ):
    """ gets a list of orders in JSON format
        potentially filtered by name/description or for a date rage based on the order creation date """

    # prepares filters 
    filters = []

    if start_date:
        try:
            start_date = start_date.timestamp()
            filters.append(f"created_at >= '{start_date}'")
        except ValueError:
            raise ValueError("start_date needs to be in the format %Y-%m-%d %H:%M:%S")
    if end_date:
        try:
            end_date = end_date.timestamp()
            filters.append(f"created_at <= '{end_date}'")
        except ValueError:
            raise ValueError("end_date needs to be in the format %Y-%m-%d %H:%M:%S")

    filter_query = " AND ".join(filters) if filters else None

    # searches in Meilisearch with date filters
    search_params = {"filter": filter_query} if filter_query else {}

    if search:
        search_results = orders_index.search(search, search_params)
    else:
        search_results = orders_index.search("", search_params)

    list_of_orders = []
    for order in search_results["hits"]:
        list_of_orders.append({
            "id": order['id'],
            "name": order['name'],
            "description": order['description'],
            "created_at": str(datetime.fromtimestamp(order['created_at'])),
            "total_amount": order['total_amount'],
            "products": order['products']
        })

    return JSONResponse(content={"orders": list_of_orders})


def crud_create_product(db: Session, product: ProductBase):
    """ creates a new product """
    new_product = Product(**product.model_dump())
    try:
        db.add(new_product)
        db.commit()
        db.refresh(new_product)
    except:
        raise HTTPException(status_code=400, detail="Error in the creation of the product")
    return new_product


def index_order_in_meilisearch(order: Order):
    """ updates the document in the index or adds it to it """

    # get list of products
    products = [
        {
            "name": op.product.name,
            "description": op.product.description,
            "price": op.product.price,
            "quantity": op.quantity
        }
        for op in order.products
    ]

    # map all the data
    order_data = {
        "id": order.id,
        "name": order.name,
        "description": order.description,
        "created_at": order.created_at.timestamp(),
        "total_amount": order.total_amount,
        "products": products  # Store full product info
    }

    orders_index.add_documents(order_data)
