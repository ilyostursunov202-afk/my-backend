from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import uuid
from enum import Enum

# Enhanced User Models with Seller Support
class UserRole(str, Enum):
    CUSTOMER = "customer"
    SELLER = "seller"
    ADMIN = "admin"

class SellerApplication(BaseModel):
    business_name: str
    business_description: str
    business_email: str
    business_phone: str
    business_address: Dict[str, str]
    tax_id: Optional[str] = None
    website: Optional[str] = None
    social_media: Optional[Dict[str, str]] = {}

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: str
    phone: Optional[str] = None
    role: UserRole = UserRole.CUSTOMER
    seller_application: Optional[SellerApplication] = None
    # Registration with shipping address
    shipping_address: Optional[Dict[str, Any]] = None

class UserUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    avatar: Optional[str] = None
    addresses: Optional[List[Dict[str, Any]]] = None
    default_shipping_address: Optional[Dict[str, Any]] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

# Phone and email verification models
class PhoneVerificationRequest(BaseModel):
    phone: str

class PhoneVerificationCheck(BaseModel):
    phone: str
    code: str

class EmailVerificationRequest(BaseModel):
    email: EmailStr

class EmailVerificationCheck(BaseModel):
    email: EmailStr
    code: str

# Password reset models  
class PasswordResetRequest(BaseModel):
    identifier: str  # Can be email or phone
    method: str = "email"  # "email" or "sms"

class PasswordResetVerify(BaseModel):
    identifier: str
    code: str
    new_password: str

class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    phone: Optional[str] = None
    phone_verified: bool = False
    email_verified: bool = False
    avatar: Optional[str] = None
    role: UserRole
    created_at: datetime
    is_active: bool = True

class UserInDB(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    email: str
    hashed_password: str
    name: str
    phone: Optional[str] = None
    phone_verified: bool = False
    email_verified: bool = False
    avatar: Optional[str] = None
    role: UserRole = UserRole.CUSTOMER
    language: str = "en"  # Default to English
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True
    addresses: List[Dict[str, Any]] = []
    default_shipping_address: Optional[Dict[str, Any]] = None

# Address Models
class Address(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str = "home"  # home, work, other
    name: str
    street: str
    city: str
    state: str
    postal_code: str
    country: str
    is_default: bool = False

# Shipping address model
class ShippingAddress(BaseModel):
    full_name: str
    address_line_1: str
    address_line_2: Optional[str] = None
    city: str
    state: str
    postal_code: str
    country: str = "US"
    phone: Optional[str] = None
    is_default: bool = False

# Token Models
class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class TokenData(BaseModel):
    email: Optional[str] = None

# Product Models (Enhanced)
class ProductCreate(BaseModel):
    name: str
    description: str
    price: float
    category: str
    brand: str
    images: List[str] = []
    inventory: int
    tags: List[str] = []

class ProductUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    images: Optional[List[str]] = None
    inventory: Optional[int] = None
    tags: Optional[List[str]] = None

class Product(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str
    price: float
    category: str
    brand: str
    images: List[str] = []
    inventory: int = 0
    rating: float = 0.0
    reviews_count: int = 0
    tags: List[str] = []
    ai_generated_description: Optional[str] = None
    seller_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True

# Review Models
class ReviewCreate(BaseModel):
    product_id: str
    rating: int = Field(..., ge=1, le=5)
    comment: str
    
class ReviewUpdate(BaseModel):
    rating: Optional[int] = Field(None, ge=1, le=5)
    comment: Optional[str] = None

class Review(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    product_id: str
    user_id: str
    rating: int
    comment: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_approved: bool = True

class ReviewResponse(BaseModel):
    id: str
    product_id: str
    user_name: str
    rating: int
    comment: str
    created_at: datetime
    is_approved: bool

# Wishlist Models
class WishlistItem(BaseModel):
    product_id: str
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class Wishlist(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    items: List[WishlistItem] = []
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Order Models (Enhanced)
class OrderStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"

class OrderItem(BaseModel):
    product_id: str
    seller_id: Optional[str] = None
    quantity: int
    price: float
    product_name: str
    
class OrderCreate(BaseModel):
    items: List[OrderItem]
    shipping_address: Address
    total_amount: float
    
class Order(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    items: List[OrderItem]
    total_amount: float
    shipping_address: Address
    status: OrderStatus = OrderStatus.PENDING
    payment_session_id: Optional[str] = None
    tracking_number: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Cart Models (Enhanced)
class CartItem(BaseModel):
    product_id: str
    quantity: int
    price: float

class Cart(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    items: List[CartItem] = []
    total: float = 0.0
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Coupon Models (Enhanced)
class CouponType(str, Enum):
    PERCENTAGE = "percentage"
    FIXED = "fixed"
    BUY_ONE_GET_ONE = "bogo"
    FREE_SHIPPING = "free_shipping"

class CouponScope(str, Enum):
    GLOBAL = "global"
    CATEGORY = "category" 
    PRODUCT = "product"
    SELLER = "seller"

class CouponCreate(BaseModel):
    code: str
    type: CouponType
    value: float  # percentage (0-100) or fixed amount
    scope: CouponScope = CouponScope.GLOBAL
    scope_value: Optional[str] = None  # category_id, product_id, or seller_id
    min_order_amount: Optional[float] = None
    max_discount: Optional[float] = None
    usage_limit: Optional[int] = None
    usage_per_user: Optional[int] = None
    starts_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    is_active: bool = True
    description: Optional[str] = None

class CouponUpdate(BaseModel):
    code: Optional[str] = None
    type: Optional[CouponType] = None
    value: Optional[float] = None
    scope: Optional[CouponScope] = None
    scope_value: Optional[str] = None
    min_order_amount: Optional[float] = None
    max_discount: Optional[float] = None
    usage_limit: Optional[int] = None
    usage_per_user: Optional[int] = None
    starts_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    is_active: Optional[bool] = None
    description: Optional[str] = None

class Coupon(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    code: str
    type: CouponType
    value: float
    scope: CouponScope = CouponScope.GLOBAL
    scope_value: Optional[str] = None
    min_order_amount: Optional[float] = None
    max_discount: Optional[float] = None
    usage_limit: Optional[int] = None
    usage_per_user: Optional[int] = None
    used_count: int = 0
    starts_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    is_active: bool = True
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class CouponUsage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    coupon_id: str
    user_id: str
    order_id: str
    discount_amount: float
    used_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Payment Transaction (Enhanced)
class PaymentTransaction(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    order_id: Optional[str] = None
    user_id: Optional[str] = None
    amount: float
    currency: str = "usd"
    status: str = "pending"  # pending, paid, failed, expired
    payment_status: str = "unpaid"
    coupon_code: Optional[str] = None
    discount_amount: Optional[float] = 0.0
    metadata: Dict[str, Any] = {}
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Seller Models (Enhanced)
class SellerStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved" 
    REJECTED = "rejected"
    SUSPENDED = "suspended"

class SellerProfile(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    business_name: str
    business_description: str
    business_email: str
    business_phone: str
    business_address: Dict[str, str]
    tax_id: Optional[str] = None
    website: Optional[str] = None
    social_media: Optional[Dict[str, str]] = {}
    commission_rate: float = 10.0  # percentage
    total_sales: float = 0.0
    total_orders: int = 0
    total_products: int = 0
    total_commission: float = 0.0
    average_rating: float = 0.0
    status: SellerStatus = SellerStatus.PENDING
    is_verified: bool = False
    verification_documents: List[str] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class SellerProfileUpdate(BaseModel):
    business_name: Optional[str] = None
    business_description: Optional[str] = None
    business_email: Optional[str] = None
    business_phone: Optional[str] = None
    business_address: Optional[Dict[str, str]] = None
    website: Optional[str] = None
    social_media: Optional[Dict[str, str]] = None

# Notification Models
class NotificationType(str, Enum):
    ORDER_CREATED = "order_created"
    ORDER_UPDATED = "order_updated"
    ORDER_SHIPPED = "order_shipped"
    ORDER_DELIVERED = "order_delivered"
    ORDER_CANCELLED = "order_cancelled"
    PAYMENT_SUCCESS = "payment_success"
    PAYMENT_FAILED = "payment_failed"
    PRODUCT_REVIEW = "product_review"
    SELLER_APPLICATION = "seller_application"
    PROMOTION = "promotion"

class NotificationChannel(str, Enum):
    EMAIL = "email"
    PUSH = "push"
    SMS = "sms"
    IN_APP = "in_app"

class NotificationTemplate(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: NotificationType
    channel: NotificationChannel
    subject_template: str
    body_template: str
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class Notification(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    type: NotificationType
    channel: NotificationChannel
    title: str
    message: str
    data: Optional[Dict[str, Any]] = None
    is_read: bool = False
    sent_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class PushSubscription(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    endpoint: str
    p256dh: str
    auth: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Commission Models
class CommissionRule(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    category: Optional[str] = None  # None means default for all categories
    commission_rate: float = 10.0  # percentage
    min_order_value: Optional[float] = None
    max_order_value: Optional[float] = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class Commission(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    order_id: str
    seller_id: str
    order_total: float
    commission_rate: float
    commission_amount: float
    status: str = "pending"  # pending, paid, disputed
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    paid_at: Optional[datetime] = None