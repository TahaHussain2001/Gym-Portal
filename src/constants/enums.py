from enum import Enum

class UserRole(str, Enum):
    ADMIN = "admin"
    RECEPTIONIST = "receptionist"

class MemberStatus(str, Enum):
    PENDING = "Pending"
    ACTIVE = "Active"
    EXPIRED = "Expired"
    ARCHIVED = "Archived"

class MembershipStatus(str, Enum):
    PENDING = "Pending"
    ACTIVE = "Active"
    EXPIRED = "Expired"

class AttendanceStatus(str, Enum):
    INSIDE = "Inside"
    COMPLETED = "Completed"

class PaymentStatus(str, Enum):
    PAID = "Paid"
    UNPAID = "Unpaid"
    PARTIAL = "Partial"

class OTPPurpose(str, Enum):
    EMAIL_VERIFICATION = "EMAIL_VERIFICATION"
    PASSWORD_RESET = "PASSWORD_RESET"

class NotificationType(str, Enum):
    NEW_MEMBER = "New Member"
    PENDING_FEE = "Pending Fee"
    EXPIRY = "Expiry"
    PAYMENT = "Payment"
