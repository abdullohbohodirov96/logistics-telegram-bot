from aiogram.fsm.state import StatesGroup, State

class DeliveryProcess(StatesGroup):
    waiting_for_source_type = State()
    waiting_for_supplier_name = State()
    waiting_for_pickup_location = State()
    waiting_for_other_place_name = State()
    
    # Transit states
    waiting_for_transit_point = State() # Recursive state for multiple points
    
    waiting_for_load_photo = State()
    waiting_for_location = State()
    waiting_for_unload_photo = State()

class AdminProcess(StatesGroup):
    waiting_for_manual_date = State()
