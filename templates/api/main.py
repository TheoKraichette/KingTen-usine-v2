"""Squelette api — FastAPI + SQLite, sans UI.

Contrat usine v2 (à ne pas casser) :
- l'API écoute sur 0.0.0.0:8000 (via le CMD du Dockerfile)
- GET /health répond 200
- la base SQLite (app.db) s'initialise seule au démarrage
- GET / renvoie une description JSON de l'API (nom, version, endpoints)
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

import db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(lifespan=lifespan, title="API", version="1.0.0")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return {
        "name": app.title,
        "version": app.version,
        "endpoints": [route.path for route in app.routes if hasattr(route, "methods")],
    }
