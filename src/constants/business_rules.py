# STHX Technologies Gym Management System Business Constants

REGISTRATION_FEE_AMOUNT = 1000.0  # PKR
UNPAID_INACTIVE_DAYS = 60         # Days unpaid before membership becomes Inactive
ARCHIVE_CLEANUP_DAYS = 60         # Days before soft-deleted member is permanently purged
EXPIRY_REMINDER_DAYS = 3          # Days before membership expiry to trigger reminder alert
DEFAULT_PLAN_DURATION_DAYS = 30   # Default duration in days for monthly plans

ROLE_ADMIN = "admin"
ROLE_SUPER_ADMIN = "super_admin"
ROLE_GYM_OWNER = "gym_owner"
ROLE_RECEPTIONIST = "receptionist"

STATUS_ACTIVE = "Active"
STATUS_PENDING = "Pending"
STATUS_EXPIRED = "Expired"
STATUS_ARCHIVED = "Archived"
