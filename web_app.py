import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from app.db import get_all_drivers_status_db
import uvicorn
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Logistics Dashboard")

# Ensure templates directory exists or create it
templates = Jinja2Templates(directory="templates")

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    drivers = get_all_drivers_status_db()
    
    total = len(drivers)
    free = sum(1 for d in drivers if d.get('status') == 'BO‘SH')
    busy = total - free
    
    return templates.TemplateResponse(
        request=request, name="dashboard.html", context={
            "drivers": drivers,
            "total": total,
            "free": free,
            "busy": busy
        }
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("web_app:app", host="0.0.0.0", port=port, reload=False)
