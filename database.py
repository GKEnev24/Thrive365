from datetime import date, timedelta
from models import db, Task, Prize, Badge


def seed_db():
    today = date.today()

    if Task.query.filter_by(date=today).count() == 0:
        tasks = [
            Task(
                title_en="Plant a Seedling at Primorski Park",
                title_bg="Засади разсад в Приморски парк",
                description_en="Visit Primorski Park (Sea Garden) and plant a seedling or water an existing plant. Document your eco-action with a photo showing the plant and your hands.",
                description_bg="Посетете Приморски парк и засадете разсад или полейте съществуващо растение. Документирайте действието си с снимка, показваща растението и ръцете ви.",
                points=50,
                location_name="Приморски парк",
                lat=42.4947,
                lng=27.4731,
                date=today
            ),
            Task(
                title_en="Pick Up Litter Near the Lake",
                title_bg="Събери боклук край Езерото",
                description_en="Spend at least 15 minutes picking up litter near Езерото (The Lake) in central Burgas. Show us the trash bag or collected waste in your photo!",
                description_bg="Прекарайте поне 15 минути в събиране на боклук около Езерото в центъра на Бургас. Покажете ни торбичката или събраните отпадъци в снимката!",
                points=75,
                location_name="Езерото, Бургас",
                lat=42.5048,
                lng=27.4580,
                date=today
            ),
            Task(
                title_en="Cycle Instead of Drive Today",
                title_bg="Карай колело вместо кола днес",
                description_en="Choose cycling over driving for your commute or errands today. Take a photo with your bike on the streets of Burgas to verify your green choice!",
                description_bg="Изберете колело вместо кола за придвижване днес. Направете снимка с колелото си по улиците на Бургас, за да потвърдите зеления си избор!",
                points=60,
                location_name="Бургас Център",
                lat=42.5048,
                lng=27.4626,
                date=today
            ),
            Task(
                title_en="Share a Nature Photo from Burgas",
                title_bg="Сподели снимка от природата на Бургас",
                description_en="Capture a beautiful nature photo from anywhere in Burgas — the sea, a park, or local flora. Show us the natural beauty of our city!",
                description_bg="Направете красива снимка от природата на Бургас — морето, парк или местна флора. Покажете ни природната красота на нашия град!",
                points=40,
                location_name="Бургас",
                lat=42.5150,
                lng=27.4700,
                date=today
            ),
            Task(
                title_en="Document Wildlife at Atanasovsko Ezero",
                title_bg="Документирай дивата природа на Атанасовско езеро",
                description_en="Visit Atanasovsko Ezero (Atanasovo Lake) and photograph birds, flamingos, or other wildlife at this unique salt lake reserve. A rare eco-adventure!",
                description_bg="Посетете Атанасовско езеро и снимайте птици, фламинго или друга дива природа в този уникален резерват. Рядко еко-приключение!",
                points=100,
                location_name="Атанасовско езеро",
                lat=42.5576,
                lng=27.5011,
                date=today
            ),
        ]
        for task in tasks:
            db.session.add(task)

    if Prize.query.count() == 0:
        prizes = [
            Prize(
                title_en="Free Coffee at Café Aroma",
                title_bg="Безплатно кафе в Café Aroma",
                description_en="Enjoy a free coffee of your choice at the popular Café Aroma in Burgas city center. Valid for any drink on the menu.",
                description_bg="Насладете се на безплатно кафе по ваш избор в популярното Café Aroma в центъра на Бургас. Важи за всяка напитка от менюто.",
                points_cost=150,
                stock=50,
                image="☕"
            ),
            Prize(
                title_en="10% Discount at Eco Market",
                title_bg="10% отстъпка в Еко Маркет",
                description_en="Get a 10% discount on your next purchase at Eco Market, the city's leading organic and sustainable grocery store.",
                description_bg="Получете 10% отстъпка от следващата си покупка в Еко Маркет, водещия магазин за органични и устойчиви продукти в града.",
                points_cost=200,
                stock=100,
                image="🛒"
            ),
            Prize(
                title_en="City Bus Day Pass",
                title_bg="Еднодневна карта за градски транспорт",
                description_en="Travel freely around Burgas for an entire day with this all-inclusive city bus day pass. Go green, skip the car!",
                description_bg="Пътувайте свободно из Бургас за цял ден с тази карта за градски транспорт. Изберете зеленото, пропуснете колата!",
                points_cost=300,
                stock=30,
                image="🚌"
            ),
            Prize(
                title_en="Thrive365 Eco Tote Bag",
                title_bg="Еко чанта Thrive365",
                description_en="Receive a premium Thrive365 branded reusable tote bag made from organic cotton. Stylish and sustainable!",
                description_bg="Получете премиум многократна чанта с марката Thrive365, изработена от органичен памук. Стилно и устойчиво!",
                points_cost=250,
                stock=75,
                image="🛍️"
            ),
            Prize(
                title_en="Free Entry to Sea Garden Events",
                title_bg="Безплатен вход за събития в Морската градина",
                description_en="Get free entry to selected cultural and eco events at the beautiful Burgas Sea Garden. Enjoy nature and culture together!",
                description_bg="Получете безплатен вход за избрани културни и еко събития в красивата Морска градина на Бургас. Насладете се на природа и култура!",
                points_cost=400,
                stock=20,
                image="🌊"
            ),
            Prize(
                title_en="Plant a Tree in Your Name",
                title_bg="Засади дърво на твое име",
                description_en="We'll plant a tree in your name in Burgas and send you an official certificate with the GPS location of your tree. Leave a lasting legacy!",
                description_bg="Ще засадим дърво на ваше име в Бургас и ще ви изпратим официален сертификат с GPS позицията на вашето дърво. Оставете траен спомен!",
                points_cost=500,
                stock=200,
                image="🌳"
            ),
        ]
        for prize in prizes:
            db.session.add(prize)

    if Badge.query.count() == 0:
        badges = [
            Badge(
                name="First Step",
                icon="🌱",
                description_en="Complete your very first eco-task",
                description_bg="Завърши първата си еко-задача",
                condition_type="tasks",
                condition_value=1
            ),
            Badge(
                name="Green Achiever",
                icon="🏆",
                description_en="Earn 100 points total",
                description_bg="Спечели общо 100 точки",
                condition_type="points",
                condition_value=100
            ),
            Badge(
                name="Eco Warrior",
                icon="⚔️",
                description_en="Earn 500 points total",
                description_bg="Спечели общо 500 точки",
                condition_type="points",
                condition_value=500
            ),
            Badge(
                name="Sustainability Champion",
                icon="🌍",
                description_en="Earn 1000 points total",
                description_bg="Спечели общо 1000 точки",
                condition_type="points",
                condition_value=1000
            ),
            Badge(
                name="Week Warrior",
                icon="🔥",
                description_en="Maintain a 7-day activity streak",
                description_bg="Поддържай 7-дневна серия от активност",
                condition_type="streak",
                condition_value=7
            ),
        ]
        for badge in badges:
            db.session.add(badge)

    db.session.commit()
