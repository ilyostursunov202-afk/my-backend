from fastapi import FastAPI, HTTPException, Request, Depends, Query, status, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pymongo import MongoClient
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
import os
import uuid
import json
import asyncio
import shutil
from dotenv import load_dotenv

# Import our custom modules
from models import *
from verification_service import verification_service

# Define SellerStats model locally since it's not in models.py
class SellerStats(BaseModel):
    total_products: int
    total_sales: float
    total_orders: int
    average_rating: float
    commission_earned: float
    monthly_sales: Dict[str, float]
    top_products: List[Dict]
    recent_orders: List[Dict]
from auth import AuthManager, get_current_user, get_current_user_required, get_admin_user, get_seller_user

# Import AI and Stripe integrations
from emergentintegrations.payments.stripe.checkout import StripeCheckout, CheckoutSessionResponse, CheckoutStatusResponse, CheckoutSessionRequest
from emergentintegrations.llm.chat import LlmChat, UserMessage

# Load environment variables
load_dotenv()

app = FastAPI(title="E-commerce API", description="Advanced E-commerce Platform with AI", version="2.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database connection
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
client = MongoClient(os.getenv("MONGODB_URI", "mongodb://localhost:27017"))  # Используем переменную окружения или локальный MongoDBMONGO_URL)
db = client["ecommerce"]

# Collections (Enhanced)
users_collection = db["users"]
products_collection = db["products"]
orders_collection = db["orders"]
cart_collection = db["cart"]
reviews_collection = db["reviews"]
wishlist_collection = db["wishlist"]
coupons_collection = db["coupons"]
coupon_usage_collection = db["coupon_usage"]
seller_profiles_collection = db["seller_profiles"]
commissions_collection = db["commissions"]
commission_rules_collection = db["commission_rules"]
notifications_collection = db["notifications"]
notification_templates_collection = db["notification_templates"]
push_subscriptions_collection = db["push_subscriptions"]
payment_transactions_collection = db["payment_transactions"]
search_collection = db["search_queries"]

# Stripe integration
STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY")
stripe_checkout = None

# AI integration
EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")
auth_manager = AuthManager()

# Helper Functions
async def generate_product_description(product_name: str, category: str, brand: str) -> str:
    """Generate AI-powered product description"""
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"product_desc_{str(uuid.uuid4())}",
            system_message="You are an expert product copywriter. Create engaging, detailed product descriptions that highlight benefits and features. Keep descriptions under 200 words and include key selling points."
        ).with_model("openai", "gpt-4o")
        
        user_message = UserMessage(
            text=f"Create a compelling product description for: {product_name} by {brand} in the {category} category. Focus on benefits, features, and what makes it special."
        )
        
        description = await chat.send_message(user_message)
        return description.strip()
    except Exception as e:
        return f"High-quality {product_name} from {brand}. Perfect for {category} enthusiasts."

async def smart_search(query: str, products: List[dict]) -> List[dict]:
    """AI-powered smart search"""
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"search_{str(uuid.uuid4())}",
            system_message="You are a smart search assistant. Given a search query and list of products, return the product IDs that best match the query in order of relevance. Return only a JSON array of product IDs."
        ).with_model("openai", "gpt-4o")
        
        products_info = [{"id": p["id"], "name": p["name"], "description": p.get("description", ""), "category": p.get("category", ""), "brand": p.get("brand", ""), "tags": p.get("tags", [])} for p in products]
        
        user_message = UserMessage(
            text=f"Search query: '{query}'\n\nProducts: {json.dumps(products_info)}\n\nReturn only a JSON array of product IDs that match the query, ordered by relevance."
        )
        
        response = await chat.send_message(user_message)
        try:
            relevant_ids = json.loads(response.strip())
            return [p for p in products if p["id"] in relevant_ids]
        except:
            return products[:10]  # Fallback to first 10
    except Exception as e:
        return products[:10]

async def get_recommendations(user_id: Optional[str] = None, product_id: Optional[str] = None) -> List[str]:
    """Generate product recommendations"""
    try:
        context = ""
        if user_id:
            orders = list(orders_collection.find({"user_id": user_id}).sort("created_at", -1).limit(5))
            if orders:
                purchased_products = []
                for order in orders:
                    for item in order.get("items", []):
                        product = products_collection.find_one({"id": item["product_id"]})
                        if product:
                            purchased_products.append(f"{product['name']} ({product['category']})")
                context = f"User's recent purchases: {', '.join(purchased_products)}"
        
        if product_id:
            product = products_collection.find_one({"id": product_id})
            if product:
                context += f" Current product: {product['name']} in {product['category']} category"
        
        all_products = list(products_collection.find({"is_active": True}).limit(20))
        products_info = [{"id": p["id"], "name": p["name"], "category": p.get("category", ""), "brand": p.get("brand", ""), "price": p.get("price", 0)} for p in all_products]
        
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"recommendations_{str(uuid.uuid4())}",
            system_message="You are a product recommendation engine. Based on user context and available products, recommend 4-6 relevant products. Return only a JSON array of product IDs."
        ).with_model("openai", "gpt-4o")
        
        user_message = UserMessage(
            text=f"Context: {context}\n\nAvailable products: {json.dumps(products_info)}\n\nRecommend 4-6 products that would interest this user. Return only a JSON array of product IDs."
        )
        
        response = await chat.send_message(user_message)
        try:
            return json.loads(response.strip())
        except:
            return [p["id"] for p in all_products[:6]]
    except Exception as e:
        return []

def calculate_average_rating(product_id: str) -> tuple[float, int]:
    """Calculate average rating and review count for a product"""
    reviews = list(reviews_collection.find({"product_id": product_id, "is_approved": True}))
    if not reviews:
        return 0.0, 0
    
    total_rating = sum(review["rating"] for review in reviews)
    avg_rating = total_rating / len(reviews)
    return round(avg_rating, 1), len(reviews)

def apply_coupon(cart_total: float, coupon_code: str, user_id: Optional[str] = None, cart_items: List[Dict] = None) -> tuple[float, str]:
    """Enhanced coupon application with advanced validation"""
    try:
        coupon = coupons_collection.find_one({
            "code": coupon_code,
            "is_active": True
        })
        
        if not coupon:
            return 0.0, "Invalid coupon code"
        
        # Check if coupon has started
        if coupon.get("starts_at") and datetime.now(timezone.utc) < coupon["starts_at"]:
            return 0.0, "Coupon is not yet active"
        
        # Check expiry
        if coupon.get("expires_at") and datetime.now(timezone.utc) > coupon["expires_at"]:
            return 0.0, "Coupon has expired"
        
        # Check global usage limit
        if coupon.get("usage_limit") and coupon.get("used_count", 0) >= coupon["usage_limit"]:
            return 0.0, "Coupon usage limit exceeded"
        
        # Check per-user usage limit
        if user_id and coupon.get("usage_per_user"):
            user_usage = coupon_usage_collection.count_documents({
                "coupon_id": coupon["id"],
                "user_id": user_id
            })
            if user_usage >= coupon["usage_per_user"]:
                return 0.0, "You have reached the usage limit for this coupon"
        
        # Check minimum order amount
        if coupon.get("min_order_amount") and cart_total < coupon["min_order_amount"]:
            return 0.0, f"Minimum order amount ${coupon['min_order_amount']:.2f} required"
        
        # Check scope (category, product, seller)
        if coupon["scope"] != "global" and cart_items:
            scope_valid = False
            eligible_total = 0.0
            
            for item in cart_items:
                product = products_collection.find_one({"id": item["product_id"]})
                if not product:
                    continue
                
                item_eligible = False
                if coupon["scope"] == "category" and product.get("category") == coupon.get("scope_value"):
                    item_eligible = True
                elif coupon["scope"] == "product" and product["id"] == coupon.get("scope_value"):
                    item_eligible = True
                elif coupon["scope"] == "seller" and product.get("seller_id") == coupon.get("scope_value"):
                    item_eligible = True
                
                if item_eligible:
                    scope_valid = True
                    eligible_total += item["quantity"] * item["price"]
            
            if not scope_valid:
                return 0.0, "Coupon is not applicable to items in your cart"
            
            # Use eligible total for scoped coupons
            if eligible_total > 0:
                cart_total = eligible_total
        
        # Calculate discount based on coupon type
        discount = 0.0
        if coupon["type"] == "percentage":
            discount = cart_total * (coupon["value"] / 100)
            if coupon.get("max_discount"):
                discount = min(discount, coupon["max_discount"])
        elif coupon["type"] == "fixed":
            discount = coupon["value"]
        elif coupon["type"] == "free_shipping":
            discount = 10.0  # Assuming $10 shipping cost
        elif coupon["type"] == "bogo":
            # Buy one get one logic (simplified)
            discount = cart_total * 0.5
        
        discount = min(discount, cart_total)  # Don't exceed cart total
        return discount, "Coupon applied successfully"
        
    except Exception as e:
        return 0.0, "Error applying coupon"

async def send_notification(user_id: str, notification_type: str, title: str, message: str, data: Dict = None, channels: List[str] = None):
    """Send notification through multiple channels"""
    try:
        if channels is None:
            channels = ["in_app", "email"]  # Default channels
        
        for channel in channels:
            notification_data = {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "type": notification_type,
                "channel": channel,
                "title": title,
                "message": message,
                "data": data or {},
                "is_read": False,
                "created_at": datetime.now(timezone.utc)
            }
            
            notifications_collection.insert_one(notification_data)
            
            # Send email notification (placeholder - would integrate with SendGrid)
            if channel == "email":
                user = users_collection.find_one({"id": user_id})
                if user:
                    print(f"EMAIL: To {user['email']} - {title}: {message}")
            
            # Send push notification (placeholder - would integrate with web push)
            elif channel == "push":
                subscription = push_subscriptions_collection.find_one({"user_id": user_id})
                if subscription:
                    print(f"PUSH: To {user_id} - {title}: {message}")
        
    except Exception as e:
        print(f"Error sending notification: {e}")

def calculate_commission(order_total: float, seller_id: str, category: str = None) -> tuple[float, float]:
    """Calculate commission for a seller"""
    try:
        # First check for category-specific rules
        commission_rule = None
        if category:
            commission_rule = commission_rules_collection.find_one({
                "category": category,
                "is_active": True,
                "$or": [
                    {"min_order_value": {"$lte": order_total}},
                    {"min_order_value": None}
                ],
                "$or": [
                    {"max_order_value": {"$gte": order_total}},
                    {"max_order_value": None}
                ]
            })
        
        # If no category rule, use default rule
        if not commission_rule:
            commission_rule = commission_rules_collection.find_one({
                "category": None,
                "is_active": True
            })
        
        # If no rule found, get seller's default rate
        if not commission_rule:
            seller = seller_profiles_collection.find_one({"user_id": seller_id})
            commission_rate = seller.get("commission_rate", 10.0) if seller else 10.0
        else:
            commission_rate = commission_rule["commission_rate"]
        
        commission_amount = order_total * (commission_rate / 100)
        return commission_rate, commission_amount
        
    except Exception as e:
        return 10.0, order_total * 0.1

# API Routes

@app.get("/")
async def root():
    return {"message": "Advanced E-commerce API is running!", "version": "2.0.0"}

# Enhanced Authentication Routes with Seller Support
@app.post("/api/auth/register", response_model=UserResponse)
async def register_user(user_data: UserCreate):
    try:
        # Check if user already exists
        existing_user = users_collection.find_one({"email": user_data.email})
        if existing_user:
            raise HTTPException(status_code=400, detail="Email already registered")
        
        # Hash password
        hashed_password = auth_manager.get_password_hash(user_data.password)
        
        # Create user
        user_dict = UserInDB(
            email=user_data.email,
            hashed_password=hashed_password,
            name=user_data.name,
            phone=user_data.phone,
            role=user_data.role
        ).dict()
        
        users_collection.insert_one(user_dict)
        
        # If registering as seller, create seller application
        if user_data.role == "seller" and user_data.seller_application:
            seller_profile_data = SellerProfile(
                user_id=user_dict["id"],
                business_name=user_data.seller_application.business_name,
                business_description=user_data.seller_application.business_description,
                business_email=user_data.seller_application.business_email,
                business_phone=user_data.seller_application.business_phone,
                business_address=user_data.seller_application.business_address,
                tax_id=user_data.seller_application.tax_id,
                website=user_data.seller_application.website,
                social_media=user_data.seller_application.social_media or {},
                status="pending"
            ).dict()
            
            seller_profiles_collection.insert_one(seller_profile_data)
            
            # Send notification to admins about new seller application
            admin_users = list(users_collection.find({"role": "admin"}))
            for admin in admin_users:
                await send_notification(
                    admin["id"],
                    "seller_application",
                    "New Seller Application",
                    f"New seller application from {user_data.name} ({user_data.seller_application.business_name})",
                    {"seller_id": user_dict["id"]},
                    ["email", "in_app"]
                )
        
        # Remove password from response
        user_dict.pop("hashed_password", None)
        user_dict.pop("_id", None)
        
        return UserResponse(**user_dict)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Seller Management Routes
@app.post("/api/sellers/apply")
async def apply_as_seller(seller_application: SellerApplication, current_user = Depends(get_current_user_required)):
    try:
        # Check if user already has a seller profile
        existing_profile = seller_profiles_collection.find_one({"user_id": current_user["user_id"]})
        if existing_profile:
            raise HTTPException(status_code=400, detail="Seller profile already exists")
        
        # Create seller profile
        seller_profile_data = SellerProfile(
            user_id=current_user["user_id"],
            business_name=seller_application.business_name,
            business_description=seller_application.business_description,
            business_email=seller_application.business_email,
            business_phone=seller_application.business_phone,
            business_address=seller_application.business_address,
            tax_id=seller_application.tax_id,
            website=seller_application.website,
            social_media=seller_application.social_media or {},
            status="pending"
        ).dict()
        
        seller_profiles_collection.insert_one(seller_profile_data)
        
        # Update user role to seller
        users_collection.update_one(
            {"id": current_user["user_id"]},
            {"$set": {"role": "seller", "updated_at": datetime.now(timezone.utc)}}
        )
        
        # Send notification to admins
        admin_users = list(users_collection.find({"role": "admin"}))
        for admin in admin_users:
            await send_notification(
                admin["id"],
                "seller_application",
                "New Seller Application",
                f"New seller application from {current_user['email']} ({seller_application.business_name})",
                {"seller_id": current_user["user_id"]},
                ["email", "in_app"]
            )
        
        seller_profile_data.pop("_id", None)
        return seller_profile_data
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/sellers/profile")
async def get_seller_profile(current_user = Depends(get_seller_user)):
    try:
        profile = seller_profiles_collection.find_one({"user_id": current_user["user_id"]})
        if not profile:
            raise HTTPException(status_code=404, detail="Seller profile not found")
        
        profile.pop("_id", None)
        return profile
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/sellers/profile")
async def update_seller_profile(profile_update: SellerProfileUpdate, current_user = Depends(get_seller_user)):
    try:
        update_data = {k: v for k, v in profile_update.dict().items() if v is not None}
        update_data["updated_at"] = datetime.now(timezone.utc)
        
        result = seller_profiles_collection.update_one(
            {"user_id": current_user["user_id"]},
            {"$set": update_data}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Seller profile not found")
        
        updated_profile = seller_profiles_collection.find_one({"user_id": current_user["user_id"]})
        updated_profile.pop("_id", None)
        
        return updated_profile
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/sellers/dashboard")
async def get_seller_dashboard(current_user = Depends(get_seller_user)):
    try:
        # Get seller profile
        seller_profile = seller_profiles_collection.find_one({"user_id": current_user["user_id"]})
        if not seller_profile:
            raise HTTPException(status_code=404, detail="Seller profile not found")
        
        # Get seller products
        products = list(products_collection.find({
            "seller_id": current_user["user_id"], 
            "is_active": True
        }))
        
        # Get seller orders
        orders = list(orders_collection.find({
            "items.seller_id": current_user["user_id"]
        }).sort("created_at", -1))
        
        # Calculate statistics
        total_products = len(products)
        total_orders = len(orders)
        total_sales = sum(order.get("total_amount", 0) for order in orders if order.get("status") == "delivered")
        
        # Get monthly sales data
        monthly_sales = {}
        for order in orders:
            if order.get("status") == "delivered":
                month = order["created_at"].strftime("%Y-%m")
                monthly_sales[month] = monthly_sales.get(month, 0) + order.get("total_amount", 0)
        
        # Get top products
        product_sales = {}
        for order in orders:
            for item in order.get("items", []):
                if item.get("seller_id") == current_user["user_id"]:
                    product_id = item["product_id"]
                    product_sales[product_id] = product_sales.get(product_id, 0) + (item["quantity"] * item["price"])
        
        top_products = []
        for product_id, sales in sorted(product_sales.items(), key=lambda x: x[1], reverse=True)[:5]:
            product = products_collection.find_one({"id": product_id})
            if product:
                product.pop("_id", None)
                product["total_sales"] = sales
                top_products.append(product)
        
        # Get recent orders
        recent_orders = []
        for order in orders[:10]:
            order.pop("_id", None)
            recent_orders.append(order)
        
        # Calculate average rating
        seller_products_ids = [p["id"] for p in products]
        reviews = list(reviews_collection.find({
            "product_id": {"$in": seller_products_ids},
            "is_approved": True
        }))
        
        average_rating = 0.0
        if reviews:
            total_rating = sum(review["rating"] for review in reviews)
            average_rating = total_rating / len(reviews)
        
        # Get commission earned
        commissions = list(commissions_collection.find({
            "seller_id": current_user["user_id"],
            "status": "paid"
        }))
        commission_earned = sum(c.get("commission_amount", 0) for c in commissions)
        
        stats = SellerStats(
            total_products=total_products,
            total_sales=total_sales,
            total_orders=total_orders,
            average_rating=round(average_rating, 1),
            commission_earned=commission_earned,
            monthly_sales=monthly_sales,
            top_products=top_products,
            recent_orders=recent_orders
        )
        
        return {
            "profile": seller_profile,
            "stats": stats.dict()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/sellers/{seller_id}/public")
async def get_seller_public_profile(seller_id: str):
    try:
        seller_profile = seller_profiles_collection.find_one({
            "user_id": seller_id,
            "status": "approved"
        })
        if not seller_profile:
            raise HTTPException(status_code=404, detail="Seller not found")
        
        # Get seller user info
        user = users_collection.find_one({"id": seller_id})
        
        # Get seller products
        products = list(products_collection.find({
            "seller_id": seller_id,
            "is_active": True
        }).limit(20))
        
        for product in products:
            product.pop("_id", None)
            avg_rating, review_count = calculate_average_rating(product["id"])
            product["rating"] = avg_rating
            product["reviews_count"] = review_count
        
        seller_profile.pop("_id", None)
        
        # Remove sensitive information
        seller_profile.pop("business_email", None)
        seller_profile.pop("business_phone", None)
        seller_profile.pop("tax_id", None)
        seller_profile.pop("commission_rate", None)
        seller_profile.pop("total_commission", None)
        
        return {
            "seller": seller_profile,
            "user_name": user["name"] if user else "Unknown",
            "products": products
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/login", response_model=Token)
async def login_user(user_data: UserLogin):
    try:
        # Find user
        user = users_collection.find_one({"email": user_data.email})
        if not user or not auth_manager.verify_password(user_data.password, user["hashed_password"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password"
            )
        
        if not user.get("is_active", True):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Account is disabled"
            )
        
        # Create tokens
        access_token = auth_manager.create_access_token(
            data={"sub": user["id"], "email": user["email"], "role": user["role"]}
        )
        refresh_token = auth_manager.create_refresh_token(
            data={"sub": user["id"], "email": user["email"]}
        )
        
        return Token(access_token=access_token, refresh_token=refresh_token)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/auth/me", response_model=UserResponse)
async def get_current_user_info(current_user = Depends(get_current_user_required)):
    try:
        user = users_collection.find_one({"id": current_user["user_id"]})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        user.pop("hashed_password", None)
        user.pop("_id", None)
        
        return UserResponse(**user)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/auth/profile", response_model=UserResponse)
async def update_user_profile(user_update: UserUpdate, current_user = Depends(get_current_user_required)):
    try:
        update_data = {k: v for k, v in user_update.dict().items() if v is not None}
        update_data["updated_at"] = datetime.now(timezone.utc)
        
        users_collection.update_one(
            {"id": current_user["user_id"]},
            {"$set": update_data}
        )
        
        updated_user = users_collection.find_one({"id": current_user["user_id"]})
        updated_user.pop("hashed_password", None)
        updated_user.pop("_id", None)
        
        return UserResponse(**updated_user)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Product Routes (Enhanced)
@app.post("/api/products", response_model=Product)
async def create_product(product: ProductCreate, current_user = Depends(get_current_user)):
    try:
        # Generate AI description
        ai_description = await generate_product_description(
            product.name, product.category, product.brand
        )
        
        product_data = product.dict()
        product_data["id"] = str(uuid.uuid4())
        product_data["ai_generated_description"] = ai_description
        product_data["created_at"] = datetime.now(timezone.utc)
        product_data["updated_at"] = datetime.now(timezone.utc)
        product_data["rating"] = 0.0
        product_data["reviews_count"] = 0
        product_data["is_active"] = True
        
        # Add seller_id if user is logged in
        if current_user:
            product_data["seller_id"] = current_user["user_id"]
        
        products_collection.insert_one(product_data)
        return Product(**product_data)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/products", response_model=List[Product])
async def get_products(
    search: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    min_price: Optional[float] = Query(None),
    max_price: Optional[float] = Query(None),
    seller_id: Optional[str] = Query(None),
    sort_by: Optional[str] = Query("created_at"),
    sort_order: Optional[str] = Query("desc"),
    limit: int = Query(20),
    current_user = Depends(get_current_user)
):
    try:
        # Build filter query
        filter_query = {"is_active": True}
        if category and category != "all":
            filter_query["category"] = {"$regex": category, "$options": "i"}
        if brand and brand != "all":
            filter_query["brand"] = {"$regex": brand, "$options": "i"}
        if seller_id:
            filter_query["seller_id"] = seller_id
        if min_price is not None or max_price is not None:
            price_filter = {}
            if min_price is not None:
                price_filter["$gte"] = min_price
            if max_price is not None:
                price_filter["$lte"] = max_price
            filter_query["price"] = price_filter
        
        # Get products
        sort_direction = -1 if sort_order == "desc" else 1
        products = list(products_collection.find(filter_query).sort(sort_by, sort_direction).limit(limit))
        
        # Convert MongoDB _id to string and remove it
        for product in products:
            product.pop("_id", None)
            # Update rating and review count
            avg_rating, review_count = calculate_average_rating(product["id"])
            product["rating"] = avg_rating
            product["reviews_count"] = review_count
        
        # Apply AI-powered search if search query provided
        if search:
            # Store search query for analytics
            search_collection.insert_one({
                "query": search,
                "results_count": len(products),
                "user_id": current_user["user_id"] if current_user else None,
                "timestamp": datetime.now(timezone.utc)
            })
            
            # Apply smart search
            products = await smart_search(search, products)
        
        return products
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/products/{product_id}", response_model=Product)
async def get_product(product_id: str):
    try:
        product = products_collection.find_one({"id": product_id, "is_active": True})
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        
        product.pop("_id", None)
        
        # Update rating and review count
        avg_rating, review_count = calculate_average_rating(product_id)
        product["rating"] = avg_rating
        product["reviews_count"] = review_count
        
        return Product(**product)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/products/{product_id}", response_model=Product)
async def update_product(product_id: str, product_update: ProductUpdate, current_user = Depends(get_current_user_required)):
    try:
        # Check if product exists
        existing_product = products_collection.find_one({"id": product_id, "is_active": True})
        if not existing_product:
            raise HTTPException(status_code=404, detail="Product not found")
        
        # Check if user owns the product or is admin
        if (existing_product.get("seller_id") != current_user["user_id"] and 
            current_user.get("role") != "admin"):
            raise HTTPException(status_code=403, detail="Not authorized to update this product")
        
        # Generate AI description if name, category, or brand changed
        update_data = {k: v for k, v in product_update.dict().items() if v is not None}
        
        ai_description = existing_product.get("ai_generated_description")
        if (update_data.get("name") != existing_product.get("name") or
            update_data.get("category") != existing_product.get("category") or
            update_data.get("brand") != existing_product.get("brand")):
            
            name = update_data.get("name", existing_product.get("name"))
            category = update_data.get("category", existing_product.get("category"))
            brand = update_data.get("brand", existing_product.get("brand"))
            ai_description = await generate_product_description(name, category, brand)
        
        update_data["ai_generated_description"] = ai_description
        update_data["updated_at"] = datetime.now(timezone.utc)
        
        # Update in database
        products_collection.update_one(
            {"id": product_id},
            {"$set": update_data}
        )
        
        # Get updated product
        updated_product = products_collection.find_one({"id": product_id})
        updated_product.pop("_id", None)
        
        # Update rating and review count
        avg_rating, review_count = calculate_average_rating(product_id)
        updated_product["rating"] = avg_rating
        updated_product["reviews_count"] = review_count
        
        return Product(**updated_product)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/products/{product_id}")
async def delete_product(product_id: str, current_user = Depends(get_current_user_required)):
    try:
        # Check if product exists
        existing_product = products_collection.find_one({"id": product_id, "is_active": True})
        if not existing_product:
            raise HTTPException(status_code=404, detail="Product not found")
        
        # Check if user owns the product or is admin
        if (existing_product.get("seller_id") != current_user["user_id"] and 
            current_user.get("role") != "admin"):
            raise HTTPException(status_code=403, detail="Not authorized to delete this product")
        
        # Soft delete product
        products_collection.update_one(
            {"id": product_id},
            {"$set": {"is_active": False, "updated_at": datetime.now(timezone.utc)}}
        )
        
        return {"message": "Product deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/products/{product_id}/recommendations")
async def get_product_recommendations(product_id: str, current_user = Depends(get_current_user)):
    try:
        user_id = current_user["user_id"] if current_user else None
        recommended_ids = await get_recommendations(user_id=user_id, product_id=product_id)
        
        recommended_products = []
        for rec_id in recommended_ids[:6]:
            product = products_collection.find_one({"id": rec_id, "is_active": True})
            if product:
                product.pop("_id", None)
                # Update rating and review count
                avg_rating, review_count = calculate_average_rating(rec_id)
                product["rating"] = avg_rating
                product["reviews_count"] = review_count
                recommended_products.append(product)
        
        return {"recommendations": recommended_products}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Review Routes
@app.post("/api/products/{product_id}/reviews", response_model=ReviewResponse)
async def create_review(product_id: str, review_data: ReviewCreate, current_user = Depends(get_current_user_required)):
    try:
        # Check if product exists
        product = products_collection.find_one({"id": product_id, "is_active": True})
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        
        # Check if user already reviewed this product
        existing_review = reviews_collection.find_one({
            "product_id": product_id,
            "user_id": current_user["user_id"]
        })
        if existing_review:
            raise HTTPException(status_code=400, detail="You have already reviewed this product")
        
        # Get user info
        user = users_collection.find_one({"id": current_user["user_id"]})
        
        # Create review
        review_dict = Review(
            product_id=product_id,
            user_id=current_user["user_id"],
            rating=review_data.rating,
            comment=review_data.comment
        ).dict()
        
        reviews_collection.insert_one(review_dict)
        
        # Prepare response
        review_dict.pop("_id", None)
        review_response = ReviewResponse(
            id=review_dict["id"],
            product_id=review_dict["product_id"],
            user_name=user["name"],
            rating=review_dict["rating"],
            comment=review_dict["comment"],
            created_at=review_dict["created_at"],
            is_approved=review_dict["is_approved"]
        )
        
        return review_response
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/products/{product_id}/reviews", response_model=List[ReviewResponse])
async def get_product_reviews(product_id: str, limit: int = Query(20), skip: int = Query(0)):
    try:
        reviews = list(reviews_collection.find({
            "product_id": product_id,
            "is_approved": True
        }).sort("created_at", -1).skip(skip).limit(limit))
        
        review_responses = []
        for review in reviews:
            review.pop("_id", None)
            user = users_collection.find_one({"id": review["user_id"]})
            
            review_response = ReviewResponse(
                id=review["id"],
                product_id=review["product_id"],
                user_name=user["name"] if user else "Anonymous",
                rating=review["rating"],
                comment=review["comment"],
                created_at=review["created_at"],
                is_approved=review["is_approved"]
            )
            review_responses.append(review_response)
        
        return review_responses
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Wishlist Routes
@app.get("/api/wishlist")
async def get_user_wishlist(current_user = Depends(get_current_user_required)):
    try:
        wishlist = wishlist_collection.find_one({"user_id": current_user["user_id"]})
        if not wishlist:
            # Create empty wishlist
            wishlist_data = Wishlist(user_id=current_user["user_id"]).dict()
            wishlist_collection.insert_one(wishlist_data)
            wishlist = wishlist_data
        
        wishlist.pop("_id", None)
        
        # Get product details for wishlist items
        products = []
        for item in wishlist.get("items", []):
            product = products_collection.find_one({"id": item["product_id"], "is_active": True})
            if product:
                product.pop("_id", None)
                # Update rating and review count
                avg_rating, review_count = calculate_average_rating(product["id"])
                product["rating"] = avg_rating
                product["reviews_count"] = review_count
                products.append(product)
        
        return {"wishlist": wishlist, "products": products}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/wishlist/add/{product_id}")
async def add_to_wishlist(product_id: str, current_user = Depends(get_current_user_required)):
    try:
        # Check if product exists
        product = products_collection.find_one({"id": product_id, "is_active": True})
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        
        # Get or create wishlist
        wishlist = wishlist_collection.find_one({"user_id": current_user["user_id"]})
        if not wishlist:
            wishlist_data = Wishlist(user_id=current_user["user_id"]).dict()
            wishlist_collection.insert_one(wishlist_data)
            wishlist = wishlist_data
        
        # Check if product already in wishlist
        existing_items = wishlist.get("items", [])
        if any(item["product_id"] == product_id for item in existing_items):
            raise HTTPException(status_code=400, detail="Product already in wishlist")
        
        # Add to wishlist
        new_item = WishlistItem(product_id=product_id).dict()
        existing_items.append(new_item)
        
        wishlist_collection.update_one(
            {"user_id": current_user["user_id"]},
            {
                "$set": {
                    "items": existing_items,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        
        return {"message": "Product added to wishlist"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/wishlist/remove/{product_id}")
async def remove_from_wishlist(product_id: str, current_user = Depends(get_current_user_required)):
    try:
        wishlist = wishlist_collection.find_one({"user_id": current_user["user_id"]})
        if not wishlist:
            raise HTTPException(status_code=404, detail="Wishlist not found")
        
        # Remove from wishlist
        existing_items = wishlist.get("items", [])
        updated_items = [item for item in existing_items if item["product_id"] != product_id]
        
        if len(updated_items) == len(existing_items):
            raise HTTPException(status_code=404, detail="Product not in wishlist")
        
        wishlist_collection.update_one(
            {"user_id": current_user["user_id"]},
            {
                "$set": {
                    "items": updated_items,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        
        return {"message": "Product removed from wishlist"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Cart Routes (Enhanced)
@app.post("/api/cart")
async def create_cart(current_user = Depends(get_current_user)):
    try:
        user_id = current_user["user_id"] if current_user else None
        session_id = str(uuid.uuid4()) if not user_id else None
        
        cart_data = Cart(
            user_id=user_id,
            session_id=session_id
        ).dict()
        
        cart_collection.insert_one(cart_data)
        cart_data.pop("_id", None)
        return cart_data
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/cart/{cart_id}")
async def get_cart(cart_id: str, current_user = Depends(get_current_user)):
    try:
        cart = cart_collection.find_one({"id": cart_id})
        if not cart:
            raise HTTPException(status_code=404, detail="Cart not found")
        
        # Check if user owns the cart
        if (current_user and cart.get("user_id") != current_user["user_id"]):
            raise HTTPException(status_code=403, detail="Not authorized to access this cart")
        
        cart.pop("_id", None)
        return cart
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/cart/{cart_id}/items")
async def add_to_cart(cart_id: str, product_id: str, quantity: int = 1, current_user = Depends(get_current_user)):
    try:
        # Get product
        product = products_collection.find_one({"id": product_id, "is_active": True})
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        
        # Check inventory
        if product["inventory"] < quantity:
            raise HTTPException(status_code=400, detail="Insufficient inventory")
        
        # Get cart
        cart = cart_collection.find_one({"id": cart_id})
        if not cart:
            raise HTTPException(status_code=404, detail="Cart not found")
        
        # Check if user owns the cart
        if (current_user and cart.get("user_id") != current_user["user_id"]):
            raise HTTPException(status_code=403, detail="Not authorized to access this cart")
        
        # Check if item already exists in cart
        items = cart.get("items", [])
        existing_item = None
        for item in items:
            if item["product_id"] == product_id:
                existing_item = item
                break
        
        if existing_item:
            existing_item["quantity"] += quantity
        else:
            items.append({
                "product_id": product_id,
                "quantity": quantity,
                "price": product["price"]
            })
        
        # Calculate total
        total = sum(item["quantity"] * item["price"] for item in items)
        
        # Update cart
        cart_collection.update_one(
            {"id": cart_id},
            {
                "$set": {
                    "items": items,
                    "total": total,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        
        updated_cart = cart_collection.find_one({"id": cart_id})
        updated_cart.pop("_id", None)
        return updated_cart
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/cart/{cart_id}/items/{product_id}")
async def remove_from_cart(cart_id: str, product_id: str, current_user = Depends(get_current_user)):
    try:
        cart = cart_collection.find_one({"id": cart_id})
        if not cart:
            raise HTTPException(status_code=404, detail="Cart not found")
        
        # Check if user owns the cart
        if (current_user and cart.get("user_id") != current_user["user_id"]):
            raise HTTPException(status_code=403, detail="Not authorized to access this cart")
        
        items = [item for item in cart.get("items", []) if item["product_id"] != product_id]
        total = sum(item["quantity"] * item["price"] for item in items)
        
        cart_collection.update_one(
            {"id": cart_id},
            {
                "$set": {
                    "items": items,
                    "total": total,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        
        updated_cart = cart_collection.find_one({"id": cart_id})
        updated_cart.pop("_id", None)
        return updated_cart
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Checkout Models
class CheckoutRequest(BaseModel):
    cart_id: str
    origin_url: str
    coupon_code: Optional[str] = None

# Checkout and Payment Routes (Enhanced)
@app.get("/api/checkout/status/{session_id}")
async def get_checkout_status(session_id: str):
    try:
        if not stripe_checkout:
            raise HTTPException(status_code=500, detail="Stripe not configured")
        
        # Get status from Stripe
        checkout_status = await stripe_checkout.get_checkout_status(session_id)
        
        # Update local transaction
        transaction = payment_transactions_collection.find_one({"session_id": session_id})
        if transaction:
            update_data = {
                "status": checkout_status.status,
                "payment_status": checkout_status.payment_status,
                "updated_at": datetime.now(timezone.utc)
            }
            
            payment_transactions_collection.update_one(
                {"session_id": session_id},
                {"$set": update_data}
            )
            
            # Update order status if payment successful
            if checkout_status.payment_status == "paid" and transaction.get("order_id"):
                orders_collection.update_one(
                    {"id": transaction["order_id"]},
                    {"$set": {"status": "processing"}}
                )
                
                # Update coupon usage count
                if transaction.get("coupon_code"):
                    coupons_collection.update_one(
                        {"code": transaction["coupon_code"]},
                        {"$inc": {"used_count": 1}}
                    )
        
        return {
            "status": checkout_status.status,
            "payment_status": checkout_status.payment_status,
            "amount_total": checkout_status.amount_total,
            "currency": checkout_status.currency
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/webhook/stripe")
async def stripe_webhook(request: Request):
    try:
        if not stripe_checkout:
            return {"status": "stripe not configured"}
        
        body = await request.body()
        signature = request.headers.get("Stripe-Signature")
        
        webhook_response = await stripe_checkout.handle_webhook(body, signature)
        
        # Update transaction based on webhook
        if webhook_response.session_id:
            update_data = {
                "payment_status": webhook_response.payment_status,
                "updated_at": datetime.now(timezone.utc)
            }
            
            payment_transactions_collection.update_one(
                {"session_id": webhook_response.session_id},
                {"$set": update_data}
            )
        
        return {"status": "success"}
        
    except Exception as e:
        return {"status": "error", "message": str(e)}

# Order Routes (Enhanced)
@app.get("/api/orders")
async def get_user_orders(current_user = Depends(get_current_user_required)):
    try:
        orders = list(orders_collection.find({"user_id": current_user["user_id"]}).sort("created_at", -1))
        for order in orders:
            order.pop("_id", None)
        
        return {"orders": orders}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/orders/{order_id}")
async def get_order_details(order_id: str, current_user = Depends(get_current_user_required)):
    try:
        order = orders_collection.find_one({"id": order_id})
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        
        # Check if user owns the order or is admin
        if (order.get("user_id") != current_user["user_id"] and 
            current_user.get("role") != "admin"):
            raise HTTPException(status_code=403, detail="Not authorized to view this order")
        
        order.pop("_id", None)
        return order
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Admin Routes
@app.get("/api/admin/users")
async def get_all_users(current_user = Depends(get_admin_user), skip: int = 0, limit: int = 50):
    try:
        users = list(users_collection.find().skip(skip).limit(limit).sort("created_at", -1))
        for user in users:
            user.pop("hashed_password", None)
            user.pop("_id", None)
        
        return {"users": users}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/orders")
async def get_all_orders(current_user = Depends(get_admin_user), skip: int = 0, limit: int = 50):
    try:
        orders = list(orders_collection.find().skip(skip).limit(limit).sort("created_at", -1))
        for order in orders:
            order.pop("_id", None)
        
        return {"orders": orders}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/admin/orders/{order_id}/status")
async def update_order_status(order_id: str, status: OrderStatus, current_user = Depends(get_admin_user)):
    try:
        order = orders_collection.find_one({"id": order_id})
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        
        orders_collection.update_one(
            {"id": order_id},
            {"$set": {"status": status.value, "updated_at": datetime.now(timezone.utc)}}
        )
        
        return {"message": "Order status updated successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Categories and filters
@app.get("/api/categories")
async def get_categories():
    try:
        categories = products_collection.distinct("category", {"is_active": True})
        return {"categories": categories}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/brands")
async def get_brands():
    try:
        brands = products_collection.distinct("brand", {"is_active": True})
        return {"brands": brands}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Analytics
@app.get("/api/analytics/search")
async def get_search_analytics(current_user = Depends(get_admin_user)):
    try:
        recent_searches = list(search_collection.find().sort("timestamp", -1).limit(10))
        for search in recent_searches:
            search.pop("_id", None)
        
        return {"recent_searches": recent_searches}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Coupon Management Routes
@app.post("/api/admin/coupons", response_model=Coupon)
async def create_coupon(coupon_data: CouponCreate, current_user = Depends(get_admin_user)):
    try:
        # Check if coupon code already exists
        existing_coupon = coupons_collection.find_one({"code": coupon_data.code})
        if existing_coupon:
            raise HTTPException(status_code=400, detail="Coupon code already exists")
        
        coupon_dict = Coupon(**coupon_data.dict()).dict()
        coupons_collection.insert_one(coupon_dict)
        
        coupon_dict.pop("_id", None)
        return Coupon(**coupon_dict)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/coupons")
async def get_all_coupons(current_user = Depends(get_admin_user), skip: int = 0, limit: int = 50):
    try:
        coupons = list(coupons_collection.find().skip(skip).limit(limit).sort("created_at", -1))
        for coupon in coupons:
            coupon.pop("_id", None)
        
        return {"coupons": coupons}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/coupons/{coupon_id}")
async def get_coupon(coupon_id: str, current_user = Depends(get_admin_user)):
    try:
        coupon = coupons_collection.find_one({"id": coupon_id})
        if not coupon:
            raise HTTPException(status_code=404, detail="Coupon not found")
        
        coupon.pop("_id", None)
        return coupon
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/admin/coupons/{coupon_id}")
async def update_coupon(coupon_id: str, coupon_update: CouponUpdate, current_user = Depends(get_admin_user)):
    try:
        existing_coupon = coupons_collection.find_one({"id": coupon_id})
        if not existing_coupon:
            raise HTTPException(status_code=404, detail="Coupon not found")
        
        # Check if new code conflicts with existing coupons
        if coupon_update.code and coupon_update.code != existing_coupon["code"]:
            conflicting_coupon = coupons_collection.find_one({"code": coupon_update.code})
            if conflicting_coupon:
                raise HTTPException(status_code=400, detail="Coupon code already exists")
        
        update_data = {k: v for k, v in coupon_update.dict().items() if v is not None}
        update_data["updated_at"] = datetime.now(timezone.utc)
        
        coupons_collection.update_one({"id": coupon_id}, {"$set": update_data})
        
        updated_coupon = coupons_collection.find_one({"id": coupon_id})
        updated_coupon.pop("_id", None)
        
        return updated_coupon
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/admin/coupons/{coupon_id}")
async def delete_coupon(coupon_id: str, current_user = Depends(get_admin_user)):
    try:
        result = coupons_collection.delete_one({"id": coupon_id})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Coupon not found")
        
        return {"message": "Coupon deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/coupons/validate")
async def validate_coupon(coupon_code: str, cart_total: float, current_user = Depends(get_current_user)):
    try:
        cart_items = []
        if current_user:
            # Get user's cart to validate scope
            cart_id = "temp"  # This would come from request in real implementation
            cart = cart_collection.find_one({"user_id": current_user["user_id"]})
            if cart:
                cart_items = cart.get("items", [])
        
        discount_amount, message = apply_coupon(
            cart_total, 
            coupon_code, 
            current_user["user_id"] if current_user else None,
            cart_items
        )
        
        return {
            "valid": discount_amount > 0,
            "discount_amount": discount_amount,
            "message": message
        }
        
    except Exception as e:
        return {
            "valid": False,
            "discount_amount": 0.0,
            "message": "Error validating coupon"
        }

# Notification Routes
@app.get("/api/notifications")
async def get_user_notifications(current_user = Depends(get_current_user_required), skip: int = 0, limit: int = 20):
    try:
        notifications = list(notifications_collection.find({
            "user_id": current_user["user_id"]
        }).skip(skip).limit(limit).sort("created_at", -1))
        
        for notification in notifications:
            notification.pop("_id", None)
        
        # Mark in-app notifications as read
        notifications_collection.update_many(
            {
                "user_id": current_user["user_id"],
                "channel": "in_app",
                "is_read": False
            },
            {
                "$set": {
                    "is_read": True,
                    "read_at": datetime.now(timezone.utc)
                }
            }
        )
        
        return {"notifications": notifications}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/notifications/push/subscribe")
async def subscribe_to_push(subscription_data: Dict, current_user = Depends(get_current_user_required)):
    try:
        # Store push subscription
        push_subscription_data = PushSubscription(
            user_id=current_user["user_id"],
            endpoint=subscription_data["endpoint"],
            p256dh=subscription_data["keys"]["p256dh"],
            auth=subscription_data["keys"]["auth"]
        ).dict()
        
        # Remove existing subscription for this user
        push_subscriptions_collection.delete_many({"user_id": current_user["user_id"]})
        
        # Add new subscription
        push_subscriptions_collection.insert_one(push_subscription_data)
        
        return {"message": "Push subscription saved successfully"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Admin Seller Management Routes
@app.get("/api/admin/sellers")
async def get_all_sellers(current_user = Depends(get_admin_user), status: Optional[str] = None, skip: int = 0, limit: int = 50):
    try:
        filter_query = {}
        if status:
            filter_query["status"] = status
        
        sellers = list(seller_profiles_collection.find(filter_query).skip(skip).limit(limit).sort("created_at", -1))
        
        # Add user information to each seller
        for seller in sellers:
            seller.pop("_id", None)
            user = users_collection.find_one({"id": seller["user_id"]})
            if user:
                seller["user_name"] = user["name"]
                seller["user_email"] = user["email"]
        
        return {"sellers": sellers}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/admin/sellers/{seller_id}/status")
async def update_seller_status(seller_id: str, status: str, current_user = Depends(get_admin_user)):
    try:
        if status not in ["approved", "rejected", "suspended"]:
            raise HTTPException(status_code=400, detail="Invalid status")
        
        seller_profile = seller_profiles_collection.find_one({"user_id": seller_id})
        if not seller_profile:
            raise HTTPException(status_code=404, detail="Seller not found")
        
        # Update seller status
        seller_profiles_collection.update_one(
            {"user_id": seller_id},
            {
                "$set": {
                    "status": status,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        
        # Send notification to seller
        title = "Seller Application Update"
        if status == "approved":
            message = "Congratulations! Your seller application has been approved. You can now start selling on our platform."
        elif status == "rejected":
            message = "Unfortunately, your seller application has been rejected. Please contact support for more information."
        else:  # suspended
            message = "Your seller account has been suspended. Please contact support immediately."
        
        await send_notification(
            seller_id,
            "seller_application",
            title,
            message,
            {"status": status},
            ["email", "in_app"]
        )
        
        return {"message": f"Seller status updated to {status}"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/admin/sellers/{seller_id}/commission")
async def update_seller_commission(seller_id: str, commission_rate: float, current_user = Depends(get_admin_user)):
    try:
        if commission_rate < 0 or commission_rate > 100:
            raise HTTPException(status_code=400, detail="Commission rate must be between 0 and 100")
        
        result = seller_profiles_collection.update_one(
            {"user_id": seller_id},
            {
                "$set": {
                    "commission_rate": commission_rate,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Seller not found")
        
        return {"message": f"Commission rate updated to {commission_rate}%"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Enhanced checkout with coupon and commission handling
@app.post("/api/checkout/session")
async def create_checkout_session(request: CheckoutRequest, current_user = Depends(get_current_user)):
    try:
        # Get cart
        cart = cart_collection.find_one({"id": request.cart_id})
        if not cart:
            raise HTTPException(status_code=404, detail="Cart not found")
        
        if not cart.get("items"):
            raise HTTPException(status_code=400, detail="Cart is empty")
        
        # Initialize Stripe checkout
        global stripe_checkout
        if not stripe_checkout and STRIPE_API_KEY:
            webhook_url = f"{request.origin_url}/api/webhook/stripe"
            stripe_checkout = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url=webhook_url)
        
        if not stripe_checkout:
            raise HTTPException(status_code=500, detail="Stripe not configured")
        
        # Calculate total
        total_amount = cart["total"]
        discount_amount = 0.0
        coupon_code = None
        
        # Apply coupon if provided
        if hasattr(request, 'coupon_code') and request.coupon_code:
            discount_amount, message = apply_coupon(
                total_amount, 
                request.coupon_code,
                current_user["user_id"] if current_user else None,
                cart.get("items", [])
            )
            if discount_amount > 0:
                coupon_code = request.coupon_code
                total_amount -= discount_amount
            else:
                raise HTTPException(status_code=400, detail=message)
        
        # Create success and cancel URLs
        success_url = f"{request.origin_url}/checkout/success?session_id={{CHECKOUT_SESSION_ID}}"
        cancel_url = f"{request.origin_url}/checkout/cancel"
        
        # Create checkout session request
        checkout_request = CheckoutSessionRequest(
            amount=total_amount,
            currency="usd",
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "cart_id": request.cart_id,
                "user_id": current_user["user_id"] if current_user else "guest",
                "session_id": cart.get("session_id", "guest"),
                "coupon_code": coupon_code or "",
                "discount_amount": str(discount_amount)
            }
        )
        
        # Create Stripe session
        session = await stripe_checkout.create_checkout_session(checkout_request)
        
        # Create order with seller information and commission calculation
        order_items = []
        total_commission = 0.0
        
        for item in cart["items"]:
            product = products_collection.find_one({"id": item["product_id"]})
            if not product:
                continue
                
            seller_id = product.get("seller_id")
            item_total = item["quantity"] * item["price"]
            
            # Calculate commission for this item
            if seller_id:
                commission_rate, commission_amount = calculate_commission(
                    item_total, 
                    seller_id, 
                    product.get("category")
                )
                total_commission += commission_amount
            
            order_items.append({
                "product_id": item["product_id"],
                "seller_id": seller_id,
                "quantity": item["quantity"],
                "price": item["price"],
                "product_name": product["name"],
                "commission_rate": commission_rate if seller_id else 0.0,
                "commission_amount": commission_amount if seller_id else 0.0
            })
        
        order_data = Order(
            user_id=current_user["user_id"] if current_user else None,
            items=order_items,
            total_amount=cart["total"],
            status=OrderStatus.PENDING,
            payment_session_id=session.session_id,
            shipping_address=Address(
                name="Default Address",
                street="123 Main St",
                city="City",
                state="State",
                postal_code="12345",
                country="US"
            )  # This should come from user input in a real app
        ).dict()
        
        # Add commission info to order
        order_data["total_commission"] = total_commission
        order_data["discount_amount"] = discount_amount
        order_data["coupon_code"] = coupon_code
        
        orders_collection.insert_one(order_data)
        
        # Create payment transaction
        transaction_data = PaymentTransaction(
            session_id=session.session_id,
            order_id=order_data["id"],
            user_id=current_user["user_id"] if current_user else None,
            amount=total_amount,
            coupon_code=coupon_code,
            discount_amount=discount_amount,
            metadata=checkout_request.metadata
        ).dict()
        
        payment_transactions_collection.insert_one(transaction_data)
        
        return {
            "url": session.url,
            "session_id": session.session_id,
            "total_amount": total_amount,
            "discount_amount": discount_amount,
            "original_amount": cart["total"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Enhanced User Management for Admin Panel
@app.get("/api/admin/users/search")
async def search_users(
    current_user = Depends(get_admin_user),
    q: Optional[str] = None,
    role: Optional[str] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50
):
    """Search and filter users with enhanced criteria"""
    try:
        query = {}
        
        # Add search filters
        if q:
            query["$or"] = [
                {"name": {"$regex": q, "$options": "i"}},
                {"email": {"$regex": q, "$options": "i"}}
            ]
        
        if role and role != "all":
            query["role"] = role
            
        if status and status != "all":
            query["is_active"] = (status == "active")
        
        # Get total count
        total_users = users_collection.count_documents(query)
        
        # Get users with pagination
        users = list(users_collection.find(query).skip(skip).limit(limit).sort("created_at", -1))
        for user in users:
            user.pop("hashed_password", None)
            user.pop("_id", None)
        
        return {
            "users": users,
            "total": total_users,
            "page": skip // limit + 1,
            "pages": (total_users + limit - 1) // limit
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/admin/users/{user_id}/status")
async def update_user_status(user_id: str, is_active: bool, current_user = Depends(get_admin_user)):
    """Block/unblock user"""
    try:
        user = users_collection.find_one({"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Don't allow admin to block themselves
        if user_id == current_user["user_id"]:
            raise HTTPException(status_code=400, detail="Cannot change your own status")
        
        users_collection.update_one(
            {"id": user_id},
            {
                "$set": {
                    "is_active": is_active,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        
        # Log admin action
        await log_admin_action(
            current_user["user_id"],
            "user_status_update",
            f"{'Activated' if is_active else 'Blocked'} user {user['email']}",
            {"user_id": user_id, "is_active": is_active}
        )
        
        return {"message": f"User {'activated' if is_active else 'blocked'} successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/admin/users/{user_id}/role")
async def update_user_role(user_id: str, role: UserRole, current_user = Depends(get_admin_user)):
    """Change user role"""
    try:
        user = users_collection.find_one({"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Don't allow admin to change their own role
        if user_id == current_user["user_id"]:
            raise HTTPException(status_code=400, detail="Cannot change your own role")
        
        old_role = user.get("role", "customer")
        
        users_collection.update_one(
            {"id": user_id},
            {
                "$set": {
                    "role": role.value,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        
        # Log admin action
        await log_admin_action(
            current_user["user_id"],
            "user_role_update",
            f"Changed user {user['email']} role from {old_role} to {role.value}",
            {"user_id": user_id, "old_role": old_role, "new_role": role.value}
        )
        
        return {"message": f"User role updated to {role.value}"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Admin Statistics and Analytics
@app.get("/api/admin/statistics")
async def get_admin_statistics(current_user = Depends(get_admin_user)):
    """Get comprehensive admin statistics"""
    try:
        # User statistics
        total_users = users_collection.count_documents({})
        active_users = users_collection.count_documents({"is_active": True})
        new_users_today = users_collection.count_documents({
            "created_at": {"$gte": datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)}
        })
        new_users_week = users_collection.count_documents({
            "created_at": {"$gte": datetime.now(timezone.utc) - timedelta(days=7)}
        })
        
        # Order statistics
        total_orders = orders_collection.count_documents({})
        orders_today = orders_collection.count_documents({
            "created_at": {"$gte": datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)}
        })
        orders_week = orders_collection.count_documents({
            "created_at": {"$gte": datetime.now(timezone.utc) - timedelta(days=7)}
        })
        
        # Revenue statistics
        revenue_pipeline = [
            {
                "$match": {
                    "status": {"$in": ["processing", "shipped", "delivered"]},
                    "total_amount": {"$exists": True}
                }
            },
            {
                "$group": {
                    "_id": None,
                    "total_revenue": {"$sum": "$total_amount"},
                    "avg_order_value": {"$avg": "$total_amount"}
                }
            }
        ]
        revenue_result = list(orders_collection.aggregate(revenue_pipeline))
        total_revenue = revenue_result[0]["total_revenue"] if revenue_result else 0
        avg_order_value = revenue_result[0]["avg_order_value"] if revenue_result else 0
        
        # Product statistics
        total_products = products_collection.count_documents({"is_active": True})
        low_stock_products = products_collection.count_documents({"inventory": {"$lt": 10}, "is_active": True})
        
        # Top selling products
        top_products_pipeline = [
            {"$unwind": "$items"},
            {
                "$group": {
                    "_id": "$items.product_id",
                    "total_sold": {"$sum": "$items.quantity"},
                    "revenue": {"$sum": {"$multiply": ["$items.quantity", "$items.price"]}}
                }
            },
            {"$sort": {"total_sold": -1}},
            {"$limit": 5}
        ]
        top_products_data = list(orders_collection.aggregate(top_products_pipeline))
        
        # Get product details for top selling
        top_products = []
        for item in top_products_data:
            product = products_collection.find_one({"id": item["_id"]})
            if product:
                top_products.append({
                    "product_id": item["_id"],
                    "name": product["name"],
                    "total_sold": item["total_sold"],
                    "revenue": item["revenue"]
                })
        
        # Recent orders
        recent_orders = list(orders_collection.find({}).sort("created_at", -1).limit(5))
        for order in recent_orders:
            order.pop("_id", None)
        
        # Website traffic (simplified - you'd typically get this from analytics)
        visits_today = search_collection.count_documents({
            "timestamp": {"$gte": datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)}
        })
        
        return {
            "user_stats": {
                "total_users": total_users,
                "active_users": active_users,
                "new_users_today": new_users_today,
                "new_users_week": new_users_week
            },
            "order_stats": {
                "total_orders": total_orders,
                "orders_today": orders_today,
                "orders_week": orders_week,
                "total_revenue": round(total_revenue, 2),
                "avg_order_value": round(avg_order_value, 2)
            },
            "product_stats": {
                "total_products": total_products,
                "low_stock_products": low_stock_products
            },
            "top_products": top_products,
            "recent_orders": recent_orders,
            "website_stats": {
                "visits_today": visits_today
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Action Logging System
action_logs_collection = db["action_logs"]

async def log_admin_action(admin_id: str, action_type: str, description: str, metadata: Dict = None):
    """Log admin actions for audit trail"""
    try:
        log_entry = {
            "id": str(uuid.uuid4()),
            "admin_id": admin_id,
            "action_type": action_type,
            "description": description,
            "metadata": metadata or {},
            "timestamp": datetime.now(timezone.utc),
            "ip_address": None  # Would be extracted from request in real implementation
        }
        action_logs_collection.insert_one(log_entry)
    except Exception as e:
        print(f"Failed to log admin action: {e}")

@app.get("/api/admin/action-logs")
async def get_action_logs(
    current_user = Depends(get_admin_user),
    action_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 50
):
    """Get admin action logs"""
    try:
        query = {}
        if action_type and action_type != "all":
            query["action_type"] = action_type
        
        total_logs = action_logs_collection.count_documents(query)
        logs = list(action_logs_collection.find(query).skip(skip).limit(limit).sort("timestamp", -1))
        
        # Get admin names
        for log in logs:
            log.pop("_id", None)
            admin = users_collection.find_one({"id": log["admin_id"]})
            log["admin_name"] = admin["name"] if admin else "Unknown Admin"
        
        return {
            "logs": logs,
            "total": total_logs,
            "page": skip // limit + 1,
            "pages": (total_logs + limit - 1) // limit
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Enhanced Profile Management
@app.get("/api/profile")
async def get_user_profile(current_user = Depends(get_current_user_required)):
    """Get current user profile"""
    try:
        user = users_collection.find_one({"id": current_user["user_id"]})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        user.pop("hashed_password", None)
        user.pop("_id", None)
        
        return user
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/profile")
async def update_user_profile(profile_data: UserUpdate, current_user = Depends(get_current_user_required)):
    """Update user profile"""
    try:
        update_data = {}
        if profile_data.name:
            update_data["name"] = profile_data.name
        if profile_data.phone:
            update_data["phone"] = profile_data.phone
        if profile_data.avatar:
            update_data["avatar"] = profile_data.avatar
        
        if update_data:
            update_data["updated_at"] = datetime.now(timezone.utc)
            users_collection.update_one(
                {"id": current_user["user_id"]},
                {"$set": update_data}
            )
        
        # Get updated user
        updated_user = users_collection.find_one({"id": current_user["user_id"]})
        updated_user.pop("hashed_password", None)
        updated_user.pop("_id", None)
        
        return updated_user
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/profile/password")
async def change_password(old_password: str, new_password: str, current_user = Depends(get_current_user_required)):
    """Change user password"""
    try:
        user = users_collection.find_one({"id": current_user["user_id"]})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Verify old password
        if not auth_manager.verify_password(old_password, user["hashed_password"]):
            raise HTTPException(status_code=400, detail="Invalid current password")
        
        # Hash new password
        new_hashed_password = auth_manager.get_password_hash(new_password)
        
        # Update password
        users_collection.update_one(
            {"id": current_user["user_id"]},
            {
                "$set": {
                    "hashed_password": new_hashed_password,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        
        return {"message": "Password updated successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# File upload handling (simplified - in production, use cloud storage)
from fastapi import UploadFile, File
import os
import shutil

@app.post("/api/profile/avatar")
async def upload_avatar(file: UploadFile = File(...), current_user = Depends(get_current_user_required)):
    """Upload user avatar"""
    try:
        if not file.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="File must be an image")
        
        # Create upload directory
        upload_dir = "/app/uploads/avatars"
        os.makedirs(upload_dir, exist_ok=True)
        
        # Generate unique filename
        file_extension = file.filename.split(".")[-1] if "." in file.filename else "jpg"
        filename = f"{current_user['user_id']}.{file_extension}"
        file_path = os.path.join(upload_dir, filename)
        
        # Save file
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Update user avatar
        avatar_url = f"/api/uploads/avatars/{filename}"
        users_collection.update_one(
            {"id": current_user["user_id"]},
            {
                "$set": {
                    "avatar": avatar_url,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        
        return {"avatar_url": avatar_url}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Serve uploaded files
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

@app.get("/api/uploads/avatars/{filename}")
async def get_avatar(filename: str):
    """Serve avatar files"""
    file_path = f"/app/uploads/avatars/{filename}"
    if os.path.exists(file_path):
        return FileResponse(file_path)
    else:
        raise HTTPException(status_code=404, detail="File not found")

# User language preference
@app.put("/api/profile/language")
async def update_language_preference(language: str, current_user = Depends(get_current_user_required)):
    """Update user language preference"""
    try:
        if language not in ["en", "ru"]:
            raise HTTPException(status_code=400, detail="Unsupported language")
        
        users_collection.update_one(
            {"id": current_user["user_id"]},
            {
                "$set": {
                    "language": language,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        
        return {"message": "Language preference updated", "language": language}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Enhanced product search endpoint
@app.get("/api/products/search")
async def search_products(
    q: Optional[str] = None,
    category: Optional[str] = None,
    brand: Optional[str] = None,
    price_range: Optional[str] = None,
    min_rating: Optional[float] = None,
    sort: Optional[str] = "name",
    limit: int = 20,
    skip: int = 0
):
    """Enhanced product search with advanced filtering and sorting"""
    try:
        query = {"is_active": True}
        
        # Text search
        if q:
            query["$or"] = [
                {"name": {"$regex": q, "$options": "i"}},
                {"brand": {"$regex": q, "$options": "i"}},
                {"category": {"$regex": q, "$options": "i"}},
                {"description": {"$regex": q, "$options": "i"}},
                {"tags": {"$regex": q, "$options": "i"}}
            ]
        
        # Category filter
        if category:
            query["$or"] = [
                {"category": {"$regex": category, "$options": "i"}},
                {"subcategory": category}
            ]
        
        # Brand filter
        if brand:
            query["brand"] = brand
        
        # Price range filter
        if price_range:
            if price_range == "1000+":
                query["price"] = {"$gte": 1000}
            else:
                try:
                    min_price, max_price = map(float, price_range.split('-'))
                    query["price"] = {"$gte": min_price, "$lte": max_price}
                except:
                    pass
        
        # Rating filter
        if min_rating:
            query["rating"] = {"$gte": min_rating}
        
        # Sorting
        sort_options = {
            "name": ("name", 1),
            "name_desc": ("name", -1),
            "price": ("price", 1),
            "price_desc": ("price", -1),
            "rating": ("rating", -1),
            "newest": ("created_at", -1)
        }
        sort_field, sort_direction = sort_options.get(sort, ("name", 1))
        
        # Execute query
        total_count = products_collection.count_documents(query)
        products = list(
            products_collection.find(query)
            .sort(sort_field, sort_direction)
            .skip(skip)
            .limit(limit)
        )
        
        # Clean up MongoDB _id field
        for product in products:
            product.pop("_id", None)
        
        return {
            "products": products,
            "total": total_count,
            "page": skip // limit + 1,
            "pages": (total_count + limit - 1) // limit,
            "limit": limit
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Enhanced Authentication with Phone and Email Verification

# Phone Verification Endpoints
@app.post("/api/auth/send-phone-verification")
async def send_phone_verification(request: PhoneVerificationRequest):
    """Send SMS verification code to phone number"""
    try:
        result = await verification_service.send_sms_verification(request.phone)
        if result["success"]:
            return {"message": result["message"], "dev_code": result.get("dev_code")}
        else:
            raise HTTPException(status_code=400, detail=result["message"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/verify-phone")
async def verify_phone(request: PhoneVerificationCheck):
    """Verify phone number with SMS code"""
    try:
        result = await verification_service.verify_sms_code(request.phone, request.code)
        if result["success"]:
            return {"message": result["message"], "verified": True}
        else:
            raise HTTPException(status_code=400, detail=result["message"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Email Verification Endpoints
@app.post("/api/auth/send-email-verification")
async def send_email_verification(request: EmailVerificationRequest):
    """Send email verification code"""
    try:
        result = await verification_service.send_email_verification(request.email)
        if result["success"]:
            return {"message": result["message"], "dev_code": result.get("dev_code")}
        else:
            raise HTTPException(status_code=400, detail=result["message"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/verify-email")
async def verify_email(request: EmailVerificationCheck):
    """Verify email with verification code"""
    try:
        result = await verification_service.verify_email_code(request.email, request.code)
        if result["success"]:
            return {"message": result["message"], "verified": True}
        else:
            raise HTTPException(status_code=400, detail=result["message"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Enhanced Registration Endpoint
@app.post("/api/auth/register-enhanced")
async def register_enhanced(user: UserCreate):
    """Enhanced user registration with optional phone and address"""
    try:
        # Check if user already exists
        if users_collection.find_one({"email": user.email}):
            raise HTTPException(status_code=400, detail="Email already registered")
        
        # Check if phone is provided and already exists
        if user.phone and users_collection.find_one({"phone": user.phone}):
            raise HTTPException(status_code=400, detail="Phone number already registered")
        
        # Create user document
        hashed_password = auth_manager.get_password_hash(user.password)
        user_doc = UserInDB(
            email=user.email,
            hashed_password=hashed_password,
            name=user.name,
            phone=user.phone,
            phone_verified=False,
            email_verified=False,
            role=user.role,
            default_shipping_address=user.shipping_address
        )
        
        # Add shipping address to addresses list if provided
        if user.shipping_address:
            user_doc.addresses = [user.shipping_address]
        
        # Save to database
        user_dict = user_doc.dict()
        users_collection.insert_one(user_dict)
        
        # Send verification codes
        verification_results = {"email": None, "phone": None}
        
        if user.email:
            email_result = await verification_service.send_email_verification(user.email)
            verification_results["email"] = email_result
        
        if user.phone:
            phone_result = await verification_service.send_sms_verification(user.phone)
            verification_results["phone"] = phone_result
        
        # Create access token
        access_token = auth_manager.create_access_token(data={"sub": user.email, "user_id": user_doc.id})
        
        # Return user data (without password)
        user_response = UserResponse(
            id=user_doc.id,
            email=user_doc.email,
            name=user_doc.name,
            phone=user_doc.phone,
            phone_verified=user_doc.phone_verified,
            email_verified=user_doc.email_verified,
            role=user_doc.role,
            created_at=user_doc.created_at,
            is_active=user_doc.is_active
        )
        
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "user": user_response,
            "verification_sent": verification_results,
            "message": "Registration successful. Please verify your email and phone."
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Password Reset Endpoints
@app.post("/api/auth/forgot-password")
async def forgot_password(request: PasswordResetRequest):
    """Send password reset code via email or SMS"""
    try:
        # Find user by email or phone
        user_query = {}
        if "@" in request.identifier:
            user_query["email"] = request.identifier
        else:
            user_query["phone"] = request.identifier
        
        user = users_collection.find_one(user_query)
        if not user:
            # Don't reveal if user exists or not for security
            return {"message": "If the account exists, you will receive a password reset code."}
        
        # Send verification code
        if request.method == "email" and user.get("email"):
            result = await verification_service.send_email_verification(
                user["email"], purpose="password_reset"
            )
        elif request.method == "sms" and user.get("phone"):
            result = await verification_service.send_sms_verification(
                user["phone"], purpose="password_reset"
            )
        else:
            raise HTTPException(status_code=400, detail="Invalid method or missing contact information")
        
        if result["success"]:
            return {
                "message": f"Password reset code sent via {request.method}",
                "dev_code": result.get("dev_code")  # Remove in production
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to send reset code")
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/reset-password")
async def reset_password(request: PasswordResetVerify):
    """Reset password with verification code"""
    try:
        # Find user by email or phone
        user_query = {}
        if "@" in request.identifier:
            user_query["email"] = request.identifier
        else:
            user_query["phone"] = request.identifier
        
        user = users_collection.find_one(user_query)
        if not user:
            raise HTTPException(status_code=400, detail="Invalid reset request")
        
        # Verify the code
        result = await verification_service.verify_email_code(
            request.identifier, request.code, purpose="password_reset"
        )
        
        if not result["success"]:
            raise HTTPException(status_code=400, detail="Invalid or expired reset code")
        
        # Update password
        hashed_password = auth_manager.get_password_hash(request.new_password)
        users_collection.update_one(
            {"_id": user["_id"]},
            {
                "$set": {
                    "hashed_password": hashed_password,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        
        # Log admin action if it's admin
        if user.get("role") == "admin":
            await log_admin_action(
                user["id"],
                "password_reset",
                f"Password reset for user {user['email']}",
                {"reset_method": "code_verification"}
            )
        
        return {"message": "Password reset successful"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Update user verification status
@app.post("/api/auth/update-verification-status")
async def update_verification_status(
    phone_verified: Optional[bool] = None,
    email_verified: Optional[bool] = None,
    current_user = Depends(get_current_user_required)
):
    """Update user's verification status after successful verification"""
    try:
        update_data = {"updated_at": datetime.now(timezone.utc)}
        
        if phone_verified is not None:
            update_data["phone_verified"] = phone_verified
        
        if email_verified is not None:
            update_data["email_verified"] = email_verified
        
        users_collection.update_one(
            {"id": current_user["user_id"]},
            {"$set": update_data}
        )
        
        return {"message": "Verification status updated successfully"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)