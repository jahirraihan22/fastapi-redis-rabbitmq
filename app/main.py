import os
from fastapi import FastAPI
from contextlib import asynccontextmanager
import redis.asyncio as redis
from pydantic import BaseModel
from dotenv import load_dotenv
import aio_pika
import logging

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://localhost")
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Connect to Redis
    app.state.redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    logger.info("Connected to Redis.")

    # Connect to RabbitMQ (using connect_robust for better recovery)
    app.state.rabbitmq = await aio_pika.connect_robust(RABBITMQ_URL)
    logger.info("Connected to RabbitMQ.")
    
    # Create channel and queue on startup
    channel = await app.state.rabbitmq.channel()
    await channel.declare_queue("items_queue", durable=True)
    await channel.close()
    
    yield

    # Close connections
    await app.state.redis.aclose()
    await app.state.rabbitmq.close()

app = FastAPI(lifespan=lifespan)

async def publish_message(key: str, value: str):
    # Use existing connection to create a new channel
    channel = await app.state.rabbitmq.channel()
    await channel.default_exchange.publish(
        aio_pika.Message(body=f"{key}:{value}".encode()),
        routing_key="items_queue",
    )
    await channel.close()
    logger.info(f"Published message to RabbitMQ: {key}:{value}")


# Declare the queue on startup
@app.on_event("startup")
async def startup_event():
    async with app.state.rabbitmq as connection:
        async with connection.channel() as channel:
            await channel.set_qos(prefetch_count=1)
            await channel.declare_queue("items_queue", durable=True)

class Item(BaseModel):
    value: str

@app.post("/store/items/{key}")
async def create_item(key: str, item: Item):
    await app.state.redis.set(key, item.value)
    await publish_message(key, item.value)
    return {"key": key, "value": item.value}

@app.get("/get/items/{key}")
async def read_item(key: str):
    value = await app.state.redis.get(key)
    return {"key": key, "value": value if value is not None else "Key not found"}

@app.get("/")
async def health_check():
    return {"status": "ok"}