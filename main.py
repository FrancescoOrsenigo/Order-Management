from fastapi import FastAPI, HTTPException, Depends, status, Query
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
from database import SessionLocal, engine
from models import Base
from schemas import ProductBase, OrderCreate, ProductCreate, OrderUpdate
from crud import crud_create_order, crud_create_product, crud_get_order_list, crud_get_order_by_id, crud_delete_order, crud_update_order
from typing import Optional
from datetime import datetime


# dependency to get database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# automatically create all required tables on startup to initialize the db
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Creating database tables...")
    Base.metadata.create_all(engine)
    yield  # The app runs here
    print("Closing database connection...")
    engine.dispose()  # Cleanup on shutdown


# initialize app
app = FastAPI(lifespan=lifespan)


@app.post("/orders", status_code=status.HTTP_201_CREATED)
def create_order(order: OrderCreate, db: Session = Depends(get_db)):
    try:
        new_order = crud_create_order(db, order)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"message": f"Order '{new_order.id}' created successfully"}


@app.put("/orders/{order_id}", status_code=status.HTTP_200_OK)
def update_order(order_id: int, order: OrderUpdate, db: Session = Depends(get_db)):
    try:
        updated_order = crud_update_order(db, order_id, order)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"message": f"Order '{order_id}' updated successfully"}


@app.delete("/orders/{order_id}")
def delete_order(order_id: int, db: Session = Depends(get_db)):

    try:
        crud_delete_order(db, order_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Order not found")
    # no raises from the function -> the deletion went well
    return {"message": f"Order '{order_id}' deleted successfully"}


@app.get("/orders/{order_id}", status_code=status.HTTP_200_OK)
def get_order(order_id: int, db: Session = Depends(get_db)):
    try:
        return crud_get_order_by_id(db, order_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/orders", status_code=status.HTTP_200_OK)
def list_orders(
        start_date: Optional[datetime] = Query(
            None, description="Filter orders created after this date"),
        end_date: Optional[datetime] = Query(
            None, description="Filter orders created before this date"),
        search: Optional[str] = Query(
            None, description="Search orders by name or description"),
        db: Session = Depends(get_db),
    ):
    order_list = crud_get_order_list(db, start_date, end_date, search)
    if order_list:
        return order_list
    # no orders found
    raise HTTPException(status_code=400, detail=str('No orders found with the search parameters provided'))


@app.post("/products", response_model=ProductCreate, status_code=status.HTTP_201_CREATED)
def create_product(product: ProductBase, db: Session = Depends(get_db)):

    try:
        return crud_create_product(db, product)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
