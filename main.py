"""
CodeLingerK - AI-Powered Code Review System
Moc 1 & 2: FastAPI + Neo4j Graph
"""

from fastapi import FastAPI
from dotenv import load_dotenv
load_dotenv()

from core.logging_config import setup_logging, get_logger
from api.routes import health_router, webhook_router, graph_router

# Load environment variables

setup_logging(level="INFO")
logger = get_logger(__name__)

app = FastAPI(
    title="CodeLingerK",
    description="AI-Powered Code Review System - Moc 1 & 2",
    version="0.2.0"
)

# Register routes
app.include_router(health_router)
app.include_router(webhook_router)
app.include_router(graph_router)
