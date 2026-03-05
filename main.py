from fastapi import FastAPI, APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict, EmailStr
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone, timedelta
import bcrypt
import jwt

# ==============================
# ENVIRONMENT
# ==============================

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

MONGO_URL = os.getenv("MONGO_URL")
DB_NAME = os.getenv("DB_NAME", "smartluz")
JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-key")

if not MONGO_URL:
    raise ValueError("MONGO_URL não configurado")

# ==============================
# DATABASE
# ==============================

client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]

# ==============================
# APP
# ==============================

app = FastAPI(title="Smart Luz API")
api_router = APIRouter(prefix="/api")
security = HTTPBearer(auto_error=False)

JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 720

# ==============================
# MODELS
# ==============================

class User(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    email: EmailStr
    name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class QuestionnaireResponse(BaseModel):
    shower_time_minutes: int
    shower_power_watts: int
    ac_hours_per_day: float
    lighting_type: str
    num_lights: int
    lights_hours_per_day: float
    standby_devices: int
    current_bill_value: float


class Diagnosis(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    responses: QuestionnaireResponse
    consumption_breakdown: Dict[str, Any]
    total_monthly_kwh: float
    estimated_monthly_cost: float
    potential_savings_kwh: float
    potential_savings_reais: float
    recommendations: List[str]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ==============================
# SECURITY
# ==============================

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_jwt_token(user_id: str, email: str):
    payload = {
        "user_id": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRATION_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Optional[str]:

    if not credentials:
        return None

    try:
        token = credentials.credentials
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("user_id")
    except:
        return None


# ==============================
# CALCULATION
# ==============================

def calculate_diagnosis(responses: QuestionnaireResponse):

    tarifa_kwh = 0.85
    days_per_month = 30

    shower_daily_kwh = (responses.shower_power_watts / 1000) * (responses.shower_time_minutes / 60)
    shower_monthly_kwh = shower_daily_kwh * days_per_month

    ac_daily_kwh = 1.5 * responses.ac_hours_per_day
    ac_monthly_kwh = ac_daily_kwh * days_per_month

    lighting_power = {
        "incandescente": 60,
        "fluorescente": 15,
        "led": 9,
    }

    light_watts = lighting_power.get(responses.lighting_type.lower(), 15)

    lighting_daily_kwh = (
        (light_watts / 1000) *
        responses.lights_hours_per_day *
        responses.num_lights
    )

    lighting_monthly_kwh = lighting_daily_kwh * days_per_month

    standby_daily_kwh = (5 / 1000) * 24 * responses.standby_devices
    standby_monthly_kwh = standby_daily_kwh * days_per_month

    total_monthly_kwh = (
        shower_monthly_kwh +
        ac_monthly_kwh +
        lighting_monthly_kwh +
        standby_monthly_kwh
    )

    estimated_monthly_cost = total_monthly_kwh * tarifa_kwh

    recommendations = []
    potential_savings_kwh = 0

    if responses.shower_time_minutes > 15:
        savings = shower_monthly_kwh * 0.3
        potential_savings_kwh += savings
        recommendations.append(
            f"Reduza o banho para 10 minutos. Economia: {savings:.1f} kWh/mês"
        )

    if responses.ac_hours_per_day > 6:
        savings = (responses.ac_hours_per_day - 6) * 1.5 * days_per_month
        potential_savings_kwh += savings
        recommendations.append(
            f"Reduza uso do ar-condicionado. Economia: {savings:.1f} kWh/mês"
        )

    if responses.lighting_type.lower() != "led":
        led_lighting = (
            (9 / 1000) *
            responses.lights_hours_per_day *
            responses.num_lights *
            days_per_month
        )

        savings = lighting_monthly_kwh - led_lighting
        potential_savings_kwh += savings

        recommendations.append(
            f"Troque lâmpadas por LED. Economia: {savings:.1f} kWh/mês"
        )

    potential_savings_reais = potential_savings_kwh * tarifa_kwh

    return {
        "consumption_breakdown": {
            "shower": round(shower_monthly_kwh, 2),
            "air_conditioning": round(ac_monthly_kwh, 2),
            "lighting": round(lighting_monthly_kwh, 2),
            "standby": round(standby_monthly_kwh, 2),
        },
        "total_monthly_kwh": round(total_monthly_kwh, 2),
        "estimated_monthly_cost": round(estimated_monthly_cost, 2),
        "potential_savings_kwh": round(potential_savings_kwh, 2),
        "potential_savings_reais": round(potential_savings_reais, 2),
        "recommendations": recommendations,
    }


# ==============================
# ROUTES
# ==============================

@api_router.get("/")
async def root():
    return {"message": "Smart Luz API funcionando 🚀"}


@api_router.post("/auth/register")
async def register(user_data: UserCreate):

    existing = await db.users.find_one({"email": user_data.email})

    if existing:
        raise HTTPException(status_code=400, detail="Email já cadastrado")

    user = User(email=user_data.email, name=user_data.name)

    user_doc = user.model_dump()
    user_doc["password"] = hash_password(user_data.password)
    user_doc["created_at"] = user_doc["created_at"].isoformat()

    await db.users.insert_one(user_doc)

    token = create_jwt_token(user.id, user.email)

    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
        },
        "token": token,
    }


@api_router.post("/auth/login")
async def login(credentials: UserLogin):

    user = await db.users.find_one({"email": credentials.email})

    if not user:
        raise HTTPException(status_code=401, detail="Usuário inválido")

    if not verify_password(credentials.password, user["password"]):
        raise HTTPException(status_code=401, detail="Senha inválida")

    token = create_jwt_token(user["id"], user["email"])

    return {
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user["name"],
        },
        "token": token,
    }


@api_router.post("/diagnosis/calculate")
async def create_diagnosis(
    responses: QuestionnaireResponse,
    user_id: Optional[str] = Depends(get_current_user),
):

    calc = calculate_diagnosis(responses)

    diagnosis = Diagnosis(
        user_id=user_id,
        responses=responses,
        **calc,
    )

    diagnosis_doc = diagnosis.model_dump()
    diagnosis_doc["created_at"] = diagnosis_doc["created_at"].isoformat()

    await db.diagnoses.insert_one(diagnosis_doc)

    return diagnosis_doc


app.include_router(api_router)

# ==============================
# CORS
# ==============================

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================
# LOG
# ==============================

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

