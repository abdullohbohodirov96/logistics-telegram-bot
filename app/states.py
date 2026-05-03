from aiogram.fsm.state import StatesGroup, State

class DeliveryProcess(StatesGroup):
    waiting_for_load_photo = State()
    waiting_for_location = State()
    waiting_for_unload_photo = State()

class AdminProcess(StatesGroup):
    waiting_for_manual_date = State()
