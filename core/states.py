from aiogram.fsm.state import State, StatesGroup

class DeliveryStates(StatesGroup):
    WAITING_TAKE = State()
    A_BLOCK = State()
    B_BLOCK = State()
    C_BLOCK = State()
    D_BLOCK = State()
    TRANSIT = State()
    LOADED_PHOTO = State()
    ON_WAY = State()
    ARRIVED_LOC = State()
    DELIVERED_PHOTO = State()
    ACT_PHOTO = State()
    FINAL_PROOF = State()
    WAITING_FINISH = State()
