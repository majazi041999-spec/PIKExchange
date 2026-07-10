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


class AddCard(StatesGroup):
    """افزودن کارت جدید: برچسب (نام/بانک) سپس متن کامل کارت."""
    waiting_label = State()
    waiting_text = State()


class ConfirmTx(StatesGroup):
    """تأیید فیش: دریافت مبلغ واریزی برای محاسبهٔ معادل."""
    waiting_amount = State()
