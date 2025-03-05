from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime

# Pydantic schemas
class ProductBase(BaseModel):
    name: str
    description: Optional[str] = None
    price: float
    stock: int

class OrderProductBase(BaseModel):
    product_id: int
    quantity: int

class OrderBase(BaseModel):
    name: str
    description: Optional[str] = None
    products: List[OrderProductBase]
    created_at: datetime
    total_amount: float

class OrderCreate(BaseModel):
    name: str
    description: Optional[str] = None
    products: List[OrderProductBase]

class OrderUpdate(OrderCreate):
    created_at: datetime

class ProductCreate(ProductBase):
    id: int
