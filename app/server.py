from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langserve import add_routes
from app.agents.agent import agent_executor

import uvicorn


app = FastAPI()
app.mount("/static", StaticFiles(directory="../static"), name="static")
templates = Jinja2Templates(directory="../templates")

# Add a route for the frontend
add_routes(app, agent_executor, disabled_endpoints=["playground"], path="/ziya")

@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)