"""Database bootstrap: non-destructive migration + seeding of the curated
Burgas eco-task library, badges and prizes."""

from sqlalchemy import inspect, text

from models import db, TaskTemplate, Prize, Badge


# ── Non-destructive migration ────────────────────────────────────────────────
# db.create_all() creates missing TABLES but never adds COLUMNS to existing ones.
# These users-table columns were added after the first release, so we ALTER them
# in idempotently for any database created before the schema grew.
_USER_COLUMNS = {
    'password_hash': 'VARCHAR(255)',
    'onboarded': 'BOOLEAN DEFAULT 0',
    'is_admin': 'BOOLEAN DEFAULT 0',
    'neighborhood': 'VARCHAR(80)',
    'transport': 'VARCHAR(20)',
    'home_type': 'VARCHAR(20)',
    'garden_access': 'VARCHAR(20)',
    'household_size': 'INTEGER',
    'interests': 'VARCHAR(255)',
    'time_commitment': 'VARCHAR(20)',
    'activity_level': 'VARCHAR(20)',
    'age_range': 'VARCHAR(20)',
}


def migrate_db():
    """Add any missing columns to the existing `users` table (SQLite-safe)."""
    insp = inspect(db.engine)
    if 'users' not in insp.get_table_names():
        return  # fresh DB — create_all() will build it with every column
    existing = {c['name'] for c in insp.get_columns('users')}
    with db.engine.begin() as conn:
        for column, ddl in _USER_COLUMNS.items():
            if column not in existing:
                conn.execute(text(f'ALTER TABLE users ADD COLUMN {column} {ddl}'))


# ── Curated, Burgas-accurate task library ────────────────────────────────────
# effort → points guide: low=30, medium=55, high=85. Tags drive eligibility:
#   needs_garden        → only users with yard/garden
#   needs_outdoor_space → users with balcony/yard/garden
#   indoor              → doable by anyone, at home
# Categories: transport, recycling, waste, nature, water, energy, food, community, education

_TEMPLATES = [
    # ── TRANSPORT ──
    dict(category='transport', effort='medium', points=55, tags='outdoor,commute',
         title_en='Cycle Instead of Driving Today',
         title_bg='Карай колело вместо кола днес',
         description_en='Replace one car trip with a bike ride in Burgas. Photograph your bike on the street or a bike lane to verify your green commute.',
         description_bg='Замени едно пътуване с кола с колело в Бургас. Снимай колелото си на улицата или във велоалея, за да потвърдиш зеленото си придвижване.',
         location_name='Бургас', lat=42.5048, lng=27.4626),
    dict(category='transport', effort='low', points=35, tags='outdoor,commute,transit',
         title_en='Take the Bus Instead of a Car',
         title_bg='Вземи автобуса вместо колата',
         description_en='Use Burgasbus public transport for a trip today. Photograph your ticket or the bus to prove your low-carbon choice.',
         description_bg='Използвай градския транспорт (Бургасбус) за пътуване днес. Снимай билета или автобуса, за да докажеш нисковъглеродния си избор.',
         location_name='Бургас', lat=42.5048, lng=27.4626),
    dict(category='transport', effort='low', points=35, tags='outdoor,walk',
         title_en='Walk a Short Errand',
         title_bg='Извърви кратка задача пеша',
         description_en='Walk instead of driving for a short errand under 2 km. Snap a photo on your walk to verify.',
         description_bg='Извърви пеша кратка задача под 2 км вместо с кола. Направи снимка по пътя за потвърждение.',
         location_name='Бургас', lat=42.5048, lng=27.4626),

    # ── RECYCLING ──
    dict(category='recycling', effort='low', points=35, tags='indoor,home',
         title_en='Sort Your Recyclables Today',
         title_bg='Раздели рециклируемите отпадъци днес',
         description_en='Separate plastic, paper and glass at home and take them to the nearest colour-coded Burgas bins. Photograph the sorted items or the bins.',
         description_bg='Раздели пластмаса, хартия и стъкло вкъщи и ги занеси до най-близките цветни контейнери в Бургас. Снимай разделените отпадъци или контейнерите.'),
    dict(category='recycling', effort='medium', points=55, tags='outdoor',
         title_en='Drop Off E-Waste or Batteries',
         title_bg='Предай електронен отпадък или батерии',
         description_en='Collect used batteries or small electronics and drop them at a Burgas e-waste / battery collection point. Photograph the items at the drop-off.',
         description_bg='Събери използвани батерии или малка електроника и ги предай в пункт за е-отпадъци в Бургас. Снимай предадените вещи на пункта.'),
    dict(category='recycling', effort='low', points=30, tags='indoor,home',
         title_en='Crush & Recycle Plastic Bottles',
         title_bg='Смачкай и рециклирай пластмасови бутилки',
         description_en='Rinse and crush your plastic bottles to save space, then recycle them. Show the collected bottles in your photo.',
         description_bg='Изплакни и смачкай пластмасовите бутилки, за да спестиш място, после ги рециклирай. Покажи събраните бутилки на снимката.'),

    # ── WASTE / CLEANUP ──
    dict(category='waste', effort='medium', points=60, tags='outdoor',
         title_en='Pick Up Litter at the Sea Garden',
         title_bg='Събери боклук в Морската градина',
         description_en='Spend 15 minutes collecting litter in Primorski Park (Sea Garden). Photograph your bag of collected trash.',
         description_bg='Прекарай 15 минути в събиране на отпадъци в Приморски парк (Морската градина). Снимай торбата със събран боклук.',
         location_name='Приморски парк', lat=42.4947, lng=27.4731),
    dict(category='waste', effort='medium', points=60, tags='outdoor',
         title_en='Clean a Stretch of Burgas Beach',
         title_bg='Почисти участък от плажа в Бургас',
         description_en='Collect litter along a stretch of the Burgas beach for 15 minutes. Show the trash you collected in your photo.',
         description_bg='Събирай отпадъци по участък от плажа в Бургас в продължение на 15 минути. Покажи събрания боклук на снимката.',
         location_name='Централен плаж', lat=42.4880, lng=27.4790),
    dict(category='waste', effort='low', points=35, tags='outdoor',
         title_en='Pick Up 5 Pieces of Litter',
         title_bg='Събери 5 боклука',
         description_en='Pick up at least 5 pieces of litter in your neighborhood and bin them properly. Photograph the litter before you throw it away.',
         description_bg='Събери поне 5 боклука в квартала си и ги изхвърли правилно. Снимай отпадъците, преди да ги изхвърлиш.'),

    # ── NATURE ──
    dict(category='nature', effort='high', points=85, tags='outdoor,needs_garden',
         title_en='Plant a Seedling or Tree',
         title_bg='Засади разсад или дърво',
         description_en='Plant a seedling, herb or tree in your garden or yard. Photograph the planted seedling with the soil and your hands.',
         description_bg='Засади разсад, билка или дърво в градината или двора си. Снимай засадения разсад с почвата и ръцете си.'),
    dict(category='nature', effort='low', points=30, tags='needs_outdoor_space,home',
         title_en='Start a Balcony Herb Pot',
         title_bg='Започни саксия с билки на балкона',
         description_en='Plant herbs (basil, mint, parsley) in a pot on your balcony. Photograph your planted pot.',
         description_bg='Засади билки (босилек, мента, магданоз) в саксия на балкона. Снимай засадената саксия.'),
    dict(category='nature', effort='high', points=85, tags='outdoor',
         title_en='Photograph Wildlife at Atanasovsko Lake',
         title_bg='Снимай дивата природа на Атанасовско езеро',
         description_en='Visit Atanasovsko Ezero salt-lake reserve and photograph birds, flamingos or other wildlife in their habitat.',
         description_bg='Посети резервата Атанасовско езеро и снимай птици, фламинго или друга дива природа в естествената им среда.',
         location_name='Атанасовско езеро', lat=42.5576, lng=27.5011),
    dict(category='nature', effort='low', points=35, tags='outdoor',
         title_en='Water Plants at Ezeroto',
         title_bg='Полей растения край Езерото',
         description_en='Water a public plant or young tree near Lake Burgas (Ezeroto). Photograph the plant you watered.',
         description_bg='Полей обществено растение или младо дърво край езерото Вая (Езерото). Снимай растението, което поля.',
         location_name='Езерото, Бургас', lat=42.5048, lng=27.4580),

    # ── WATER ──
    dict(category='water', effort='low', points=30, tags='indoor,home',
         title_en='Take a 4-Minute Shower',
         title_bg='Вземи 4-минутен душ',
         description_en='Cut your shower to 4 minutes to save water. Photograph a timer or clock showing your short shower time.',
         description_bg='Намали душа си до 4 минути, за да пестиш вода. Снимай таймер или часовник, показващ краткото време.'),
    dict(category='water', effort='medium', points=50, tags='indoor,home',
         title_en='Fix or Report a Dripping Tap',
         title_bg='Поправи или докладвай течащ кран',
         description_en='Fix a leaking tap at home or report a public water leak. Photograph the tap or your report.',
         description_bg='Поправи течащ кран вкъщи или докладвай обществена водна течь. Снимай крана или сигнала си.'),
    dict(category='water', effort='low', points=30, tags='needs_outdoor_space,home',
         title_en='Collect Rainwater for Plants',
         title_bg='Събери дъждовна вода за растения',
         description_en='Place a container to collect rainwater and use it for your plants. Photograph your rainwater collector.',
         description_bg='Постави съд за събиране на дъждовна вода и я използвай за растенията си. Снимай съда за дъждовна вода.'),

    # ── ENERGY ──
    dict(category='energy', effort='low', points=30, tags='indoor,home',
         title_en='Unplug Standby Devices',
         title_bg='Изключи уредите от режим на готовност',
         description_en='Unplug devices on standby (TV, chargers, microwave) to cut phantom power. Photograph the unplugged sockets.',
         description_bg='Изключи уредите в режим на готовност (телевизор, зарядни, микровълнова), за да спреш скритата консумация. Снимай изключените контакти.'),
    dict(category='energy', effort='low', points=30, tags='indoor,home',
         title_en='Line-Dry Your Laundry',
         title_bg='Простирай прането на въздух',
         description_en='Skip the dryer and hang your laundry to dry naturally. Photograph your drying laundry.',
         description_bg='Пропусни сушилнята и простри прането да изсъхне на въздух. Снимай простряното пране.'),
    dict(category='energy', effort='medium', points=50, tags='indoor,home',
         title_en='Switch a Bulb to LED',
         title_bg='Смени крушка с LED',
         description_en='Replace an old bulb with an energy-saving LED. Photograph the new LED bulb installed.',
         description_bg='Смени стара крушка с енергоспестяваща LED. Снимай новата LED крушка, монтирана на място.'),

    # ── FOOD ──
    dict(category='food', effort='low', points=35, tags='outdoor,market',
         title_en='Shop at a Local Burgas Market',
         title_bg='Пазарувай от местен пазар в Бургас',
         description_en='Buy local, seasonal produce from a Burgas farmers market instead of imported goods. Photograph your local produce.',
         description_bg='Купи местни, сезонни продукти от пазар в Бургас вместо вносни стоки. Снимай местните продукти.',
         location_name='Централен пазар, Бургас', lat=42.5012, lng=27.4710),
    dict(category='food', effort='low', points=30, tags='indoor,home',
         title_en='Cook a Meat-Free Meal',
         title_bg='Сготви ястие без месо',
         description_en='Prepare one plant-based, meat-free meal to lower your carbon footprint. Photograph your finished dish.',
         description_bg='Приготви едно растително ястие без месо, за да намалиш въглеродния си отпечатък. Снимай готовото ястие.'),
    dict(category='food', effort='medium', points=55, tags='needs_garden,home',
         title_en='Start a Compost for Food Scraps',
         title_bg='Започни компост за хранителни остатъци',
         description_en='Start composting fruit and vegetable scraps in your yard or garden. Photograph your compost setup.',
         description_bg='Започни да компостираш плодови и зеленчукови остатъци в двора или градината си. Снимай компостера си.'),

    # ── COMMUNITY ──
    dict(category='community', effort='high', points=80, tags='outdoor,community',
         title_en='Organize a Mini Cleanup with Friends',
         title_bg='Организирай мини почистване с приятели',
         description_en='Gather 2+ friends and clean a shared space in your neighborhood. Photograph your group with the collected litter.',
         description_bg='Събери 2+ приятели и почистете общо пространство в квартала. Снимай групата със събрания боклук.'),
    dict(category='community', effort='low', points=35, tags='indoor,community',
         title_en='Report Illegal Dumping in Burgas',
         title_bg='Сигнализирай за нерегламентирано сметище',
         description_en='Report an illegal dump or overflowing bin to the Burgas municipality app/hotline. Photograph the spot you reported.',
         description_bg='Сигнализирай за нерегламентирано сметище или препълнен контейнер към Община Бургас. Снимай мястото, за което сигнализира.'),

    # ── EDUCATION ──
    dict(category='education', effort='low', points=30, tags='indoor,anytime',
         title_en='Share an Eco-Tip with Someone',
         title_bg='Сподели еко-съвет с някого',
         description_en='Teach a friend or family member one sustainability tip for Burgas. Photograph a note or screenshot of the tip you shared.',
         description_bg='Научи приятел или близък на един съвет за устойчивост за Бургас. Снимай бележка или екранна снимка на споделения съвет.'),
]


def seed_templates():
    if TaskTemplate.query.count() == 0:
        for t in _TEMPLATES:
            db.session.add(TaskTemplate(**t))
        db.session.commit()


def seed_prizes():
    if Prize.query.count() > 0:
        return
    prizes = [
        Prize(title_en="Free Coffee at Café Aroma", title_bg="Безплатно кафе в Café Aroma",
              description_en="Enjoy a free coffee of your choice at the popular Café Aroma in Burgas city center. Valid for any drink on the menu.",
              description_bg="Насладете се на безплатно кафе по ваш избор в популярното Café Aroma в центъра на Бургас. Важи за всяка напитка от менюто.",
              points_cost=150, stock=50, image="☕"),
        Prize(title_en="10% Discount at Eco Market", title_bg="10% отстъпка в Еко Маркет",
              description_en="Get a 10% discount on your next purchase at Eco Market, the city's leading organic and sustainable grocery store.",
              description_bg="Получете 10% отстъпка от следващата си покупка в Еко Маркет, водещия магазин за органични и устойчиви продукти в града.",
              points_cost=200, stock=100, image="🛒"),
        Prize(title_en="City Bus Day Pass", title_bg="Еднодневна карта за градски транспорт",
              description_en="Travel freely around Burgas for an entire day with this all-inclusive city bus day pass. Go green, skip the car!",
              description_bg="Пътувайте свободно из Бургас за цял ден с тази карта за градски транспорт. Изберете зеленото, пропуснете колата!",
              points_cost=300, stock=30, image="🚌"),
        Prize(title_en="Thrive365 Eco Tote Bag", title_bg="Еко чанта Thrive365",
              description_en="Receive a premium Thrive365 branded reusable tote bag made from organic cotton. Stylish and sustainable!",
              description_bg="Получете премиум многократна чанта с марката Thrive365, изработена от органичен памук. Стилно и устойчиво!",
              points_cost=250, stock=75, image="🛍️"),
        Prize(title_en="Free Entry to Sea Garden Events", title_bg="Безплатен вход за събития в Морската градина",
              description_en="Get free entry to selected cultural and eco events at the beautiful Burgas Sea Garden. Enjoy nature and culture together!",
              description_bg="Получете безплатен вход за избрани културни и еко събития в красивата Морска градина на Бургас. Насладете се на природа и култура!",
              points_cost=400, stock=20, image="🌊"),
        Prize(title_en="Plant a Tree in Your Name", title_bg="Засади дърво на твое име",
              description_en="We'll plant a tree in your name in Burgas and send you an official certificate with the GPS location of your tree. Leave a lasting legacy!",
              description_bg="Ще засадим дърво на ваше име в Бургас и ще ви изпратим официален сертификат с GPS позицията на вашето дърво. Оставете траен спомен!",
              points_cost=500, stock=200, image="🌳"),
    ]
    db.session.add_all(prizes)
    db.session.commit()


def seed_badges():
    if Badge.query.count() > 0:
        return
    badges = [
        Badge(name="First Step", icon="🌱", description_en="Complete your very first eco-task",
              description_bg="Завърши първата си еко-задача", condition_type="tasks", condition_value=1),
        Badge(name="Green Achiever", icon="🏆", description_en="Earn 100 points total",
              description_bg="Спечели общо 100 точки", condition_type="points", condition_value=100),
        Badge(name="Eco Warrior", icon="⚔️", description_en="Earn 500 points total",
              description_bg="Спечели общо 500 точки", condition_type="points", condition_value=500),
        Badge(name="Sustainability Champion", icon="🌍", description_en="Earn 1000 points total",
              description_bg="Спечели общо 1000 точки", condition_type="points", condition_value=1000),
        Badge(name="Week Warrior", icon="🔥", description_en="Maintain a 7-day activity streak",
              description_bg="Поддържай 7-дневна серия от активност", condition_type="streak", condition_value=7),
    ]
    db.session.add_all(badges)
    db.session.commit()


def seed_db():
    """Run migration + seed reference data. Safe to call on every startup."""
    migrate_db()
    seed_templates()
    seed_prizes()
    seed_badges()
