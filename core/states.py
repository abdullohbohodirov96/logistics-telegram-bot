from aiogram.fsm.state import State, StatesGroup

class DeliveryStates(StatesGroup):
    WAITING_TAKE = State()
    BLOCK_MENU = State()
    BLOCK_SUBMENU = State()
    A_BLOCK = State()
    B_BLOCK = State()
    C_BLOCK = State()
    D_BLOCK = State()
    TRANSIT = State()
    LOADED_PHOTO = State()
    ON_WAY = State()
    ARRIVED_CLIENT = State()
    DELIVERED_PHOTO = State()
    ACT_PHOTO = State()
    DELIVERED_LOC = State()
    WAITING_FINISH = State()

class AdminProcess(StatesGroup):
    waiting_for_manual_date = State()
    waiting_for_order_id = State()
    waiting_for_driver = State()
    waiting_for_message = State()
    waiting_for_photo = State()
    waiting_for_location = State()
