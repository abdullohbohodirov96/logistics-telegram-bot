with open("core/handlers/delivery.py", "r") as f:
    content = f.read()

content = content.replace(
    'line_tr = get_emoji_line("🚚 Transit", order.get(\'transit_status\'), order.get(\'d_block_at\'), order.get(\'transit_at\'), "OLDI", "🚚", "🚚")',
    'line_tr = get_emoji_line("Transit", order.get(\'transit_status\'), order.get(\'d_block_at\'), order.get(\'transit_at\'), "OLDI", "🚚", "🚚")'
)

content = content.replace(
    'line_yuk = get_emoji_line("📸 Yuk rasmi", "", order.get(\'transit_at\'), order.get(\'loaded_photo_at\'), "", "📸", "📸")',
    'line_yuk = get_emoji_line("Yuk rasmi", "", order.get(\'transit_at\'), order.get(\'loaded_photo_at\'), "", "📸", "📸")'
)

content = content.replace(
    'line_way = get_emoji_line("🛣 Yo\'lga chiqish", "", order.get(\'loaded_photo_at\'), order.get(\'on_way_at\'), "", "🛣", "🛣")',
    'line_way = get_emoji_line("Yo\'lga chiqish", "", order.get(\'loaded_photo_at\'), order.get(\'on_way_at\'), "", "🛣", "🛣")'
)

content = content.replace(
    'line_act = get_emoji_line("🧾 Akt rasmi", "", order.get(\'on_way_at\'), order.get(\'act_photo_at\'), "", "🧾", "🧾")',
    'line_act = get_emoji_line("Akt rasmi", "", order.get(\'on_way_at\'), order.get(\'act_photo_at\'), "", "🧾", "🧾")'
)

content = content.replace(
    'line_loc = get_emoji_line("📍 Lokatsiya", "", order.get(\'act_photo_at\'), order.get(\'delivered_location_at\'), "", "📍", "🟥")',
    'line_loc = get_emoji_line("Lokatsiya", "", order.get(\'act_photo_at\'), order.get(\'delivered_location_at\'), "", "📍", "🟥")'
)

with open("core/handlers/delivery.py", "w") as f:
    f.write(content)
