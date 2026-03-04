from fastapi import FastAPI, APIRouter, HTTPException, Depends, status
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

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI()
api_router = APIRouter(prefix="/api")
security = HTTPBearer(auto_error=False)

JWT_SECRET = os.environ.get('JWT_SECRET', 'your-secret-key-change-in-production')
JWT_ALGORITHM = 'HS256'
JWT_EXPIRATION_HOURS = 720

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

class DiagnosisResponse(BaseModel):
    id: str
    responses: QuestionnaireResponse
    consumption_breakdown: Dict[str, Any]
    total_monthly_kwh: float
    estimated_monthly_cost: float
    potential_savings_kwh: float
    potential_savings_reais: float
    recommendations: List[str]
    created_at: str

class DiagnosisHistory(BaseModel):
    diagnoses: List[DiagnosisResponse]

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def create_jwt_token(user_id: str, email: str) -> str:
    payload = {
        'user_id': user_id,
        'email': email,
        'exp': datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRATION_HOURS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Optional[str]:
    if not credentials:
        return None
    try:
        token = credentials.credentials
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get('user_id')
    except:
        return None

def calculate_diagnosis(responses: QuestionnaireResponse) -> Dict[str, Any]:
    tarifa_kwh = 0.85
    days_per_month = 30
    
    shower_daily_kwh = (responses.shower_power_watts / 1000) * (responses.shower_time_minutes / 60)
    shower_monthly_kwh = shower_daily_kwh * days_per_month
    shower_monthly_cost = shower_monthly_kwh * tarifa_kwh
    
    ac_daily_kwh = 1.5 * responses.ac_hours_per_day
    ac_monthly_kwh = ac_daily_kwh * days_per_month
    ac_monthly_cost = ac_monthly_kwh * tarifa_kwh
    
    lighting_power = {
        'incandescente': 60,
        'fluorescente': 15,
        'led': 9
    }
    light_watts = lighting_power.get(responses.lighting_type.lower(), 15)
    lighting_daily_kwh = (light_watts / 1000) * responses.lights_hours_per_day * responses.num_lights
    lighting_monthly_kwh = lighting_daily_kwh * days_per_month
    lighting_monthly_cost = lighting_monthly_kwh * tarifa_kwh
    
    standby_daily_kwh = (5 / 1000) * 24 * responses.standby_devices
    standby_monthly_kwh = standby_daily_kwh * days_per_month
    standby_monthly_cost = standby_monthly_kwh * tarifa_kwh
    
    total_monthly_kwh = shower_monthly_kwh + ac_monthly_kwh + lighting_monthly_kwh + standby_monthly_kwh
    estimated_monthly_cost = total_monthly_kwh * tarifa_kwh
    
    recommendations = []
    potential_savings_kwh = 0
    
    if responses.shower_time_minutes > 15:
        savings = ((responses.shower_time_minutes - 10) / responses.shower_time_minutes) * shower_monthly_kwh
        potential_savings_kwh += savings
        recommendations.append(f"Reduza o tempo de banho para 10 minutos. Economia estimada: {savings:.1f} kWh/mês (R$ {savings * tarifa_kwh:.2f})")
    
    if responses.ac_hours_per_day > 6:
        savings = (responses.ac_hours_per_day - 6) * 1.5 * days_per_month
        potential_savings_kwh += savings
        recommendations.append(f"Reduza o uso do ar-condicionado para 6 horas/dia e use ventilador quando possível. Economia estimada: {savings:.1f} kWh/mês (R$ {savings * tarifa_kwh:.2f})")
    
    if responses.lighting_type.lower() != 'led':
        current_lighting = lighting_monthly_kwh
        led_lighting = (9 / 1000) * responses.lights_hours_per_day * responses.num_lights * days_per_month
        savings = current_lighting - led_lighting
        potential_savings_kwh += savings
        recommendations.append(f"Substitua suas lâmpadas por LED. Economia estimada: {savings:.1f} kWh/mês (R$ {savings * tarifa_kwh:.2f})")
    
    if responses.standby_devices > 3:
        savings = standby_monthly_kwh * 0.7
        potential_savings_kwh += savings
        recommendations.append(f"Desligue aparelhos da tomada quando não estiver usando. Economia estimada: {savings:.1f} kWh/mês (R$ {savings * tarifa_kwh:.2f})")
    
    if not recommendations:
        recommendations.append("Seus hábitos de consumo já são muito bons! Continue assim.")
        recommendations.append("Considere investir em painéis solares para reduzir ainda mais sua conta.")
    
    potential_savings_reais = potential_savings_kwh * tarifa_kwh
    
    return {
        'consumption_breakdown': {
            'shower': {'kwh': round(shower_monthly_kwh, 2), 'cost': round(shower_monthly_cost, 2)},
            'air_conditioning': {'kwh': round(ac_monthly_kwh, 2), 'cost': round(ac_monthly_cost, 2)},
            'lighting': {'kwh': round(lighting_monthly_kwh, 2), 'cost': round(lighting_monthly_cost, 2)},
            'standby': {'kwh': round(standby_monthly_kwh, 2), 'cost': round(standby_monthly_cost, 2)}
        },
        'total_monthly_kwh': round(total_monthly_kwh, 2),
        'estimated_monthly_cost': round(estimated_monthly_cost, 2),
        'potential_savings_kwh': round(potential_savings_kwh, 2),
        'potential_savings_reais': round(potential_savings_reais, 2),
        'recommendations': recommendations
    }

@api_router.get("/")
async def root():
    return {"message": "Smart Luz API"}

@api_router.post("/auth/register")
async def register(user_data: UserCreate):
    existing_user = await db.users.find_one({'email': user_data.email}, {'_id': 0})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email já cadastrado")
    
    user = User(email=user_data.email, name=user_data.name)
    user_doc = user.model_dump()
    user_doc['password'] = hash_password(user_data.password)
    user_doc['created_at'] = user_doc['created_at'].isoformat()
    
    await db.users.insert_one(user_doc)
    
    token = create_jwt_token(user.id, user.email)
    return {
        'user': {'id': user.id, 'email': user.email, 'name': user.name},
        'token': token
    }

@api_router.post("/auth/login")
async def login(credentials: UserLogin):
    user_doc = await db.users.find_one({'email': credentials.email}, {'_id': 0})
    if not user_doc or not verify_password(credentials.password, user_doc['password']):
        raise HTTPException(status_code=401, detail="Email ou senha incorretos")
    
    token = create_jwt_token(user_doc['id'], user_doc['email'])
    return {
        'user': {'id': user_doc['id'], 'email': user_doc['email'], 'name': user_doc['name']},
        'token': token
    }

@api_router.post("/diagnosis/calculate")
async def create_diagnosis(responses: QuestionnaireResponse, user_id: Optional[str] = Depends(get_current_user)):
    calc_results = calculate_diagnosis(responses)
    
    diagnosis = Diagnosis(
        user_id=user_id,
        responses=responses,
        **calc_results
    )
    
    diagnosis_doc = diagnosis.model_dump()
    diagnosis_doc['created_at'] = diagnosis_doc['created_at'].isoformat()
    
    await db.diagnoses.insert_one(diagnosis_doc)
    
    return {
        'id': diagnosis.id,
        'responses': responses.model_dump(),
        **calc_results,
        'created_at': diagnosis.created_at.isoformat()
    }

@api_router.get("/diagnosis/history")
async def get_diagnosis_history(user_id: Optional[str] = Depends(get_current_user)):
    if not user_id:
        raise HTTPException(status_code=401, detail="Autenticação necessária")
    
    diagnoses = await db.diagnoses.find({'user_id': user_id}, {'_id': 0}).sort('created_at', -1).to_list(100)
    
    return {'diagnoses': diagnoses}

@api_router.get("/diagnosis/{diagnosis_id}")
async def get_diagnosis(diagnosis_id: str):
    diagnosis = await db.diagnoses.find_one({'id': diagnosis_id}, {'_id': 0})
    if not diagnosis:
        raise HTTPException(status_code=404, detail="Diagnóstico não encontrado")
    return diagnosis

app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

