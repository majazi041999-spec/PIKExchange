from aiogram.fsm.state import State, StatesGroup


class ReceiptFlow(StatesGroup):
    waiting_receipt = State()


class Broadcast(StatesGroup):
    waiting_text = State()


class AdminEdit(StatesGroup):
    """ورود مقدار جدید برای یک تنظیم (کلید در FSM data نگه داشته می‌شود)."""
    waiting_value = State()


class AdminWallet(StatesGroup):
    waiting_user = State()
    waiting_amount = State()


class AdminMessageUser(StatesGroup):
    """ارسال پیام مستقیم ادمین به یک کاربر از طریق ربات."""
    waiting_text = State()
