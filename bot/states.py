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
    waiting_amount = State()       # تغییر تومان
    waiting_amount_rub = State()   # تغییر روبل


class AdminMessageUser(StatesGroup):
    """ارسال پیام مستقیم ادمین به یک کاربر از طریق ربات."""
    waiting_text = State()


class AddCard(StatesGroup):
    """افزودن کارت جدید: برچسب، سپس عکس/متن کارت، سپس شماره کارت و شبا (برای دکمهٔ کپی)."""
    waiting_label = State()
    waiting_content = State()   # عکس یا متن
    waiting_number = State()    # شماره کارت (برای کپی)
    waiting_sheba = State()     # شماره شبا (برای کپی)


class PayoutFlow(StatesGroup):
    """ثبت اطلاعات کارت روسی کاربر پس از تأیید فیش (برای فاکتور و واریز روبل)."""
    waiting_info = State()


class ConfirmTx(StatesGroup):
    """تأیید فیش: دریافت مبلغ واریزی برای محاسبهٔ معادل."""
    waiting_amount = State()
