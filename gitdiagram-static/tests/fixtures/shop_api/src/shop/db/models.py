"""ORM models for products, stock and orders."""

from sqlalchemy import Column, Integer, Numeric, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Product(Base):
    """A sellable product."""

    __tablename__ = "products"
    sku = Column(String, primary_key=True)
    name = Column(String)
    price = Column(Numeric)


class Stock(Base):
    """Stock level for a product."""

    __tablename__ = "stock"
    id = Column(Integer, primary_key=True)
    sku = Column(String)
    reserved = Column(Integer, default=0)


class Order(Base):
    """A customer order."""

    __tablename__ = "orders"
    id = Column(String, primary_key=True)
    total = Column(Numeric)
