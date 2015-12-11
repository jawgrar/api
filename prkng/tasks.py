# -*- coding: utf-8 -*-

from prkng import create_app, notifications
from prkng.database import PostgresWrapper

import boto.sns
import datetime
import demjson
import json
import os
import pytz
import re
from redis import Redis
import requests
from rq_scheduler import Scheduler
from subprocess import check_call
from suds.client import Client

scheduler = Scheduler('scheduled_jobs', connection=Redis(db=1))


def init_tasks(debug=True):
    now = datetime.datetime.now()
    stop_tasks()
    scheduler.schedule(scheduled_time=now, func=update_car2go, interval=120, result_ttl=240, repeat=None)
    scheduler.schedule(scheduled_time=now, func=update_automobile, interval=120, result_ttl=240, repeat=None)
    scheduler.schedule(scheduled_time=now, func=update_communauto, interval=120, result_ttl=240, repeat=None)
    scheduler.schedule(scheduled_time=now, func=update_zipcar, interval=86400, result_ttl=172800, repeat=None)
    scheduler.schedule(scheduled_time=now, func=update_analytics, interval=120, result_ttl=240, repeat=None)
    scheduler.schedule(scheduled_time=now, func=update_parkingpanda, interval=120, result_ttl=240, repeat=None)
    scheduler.schedule(scheduled_time=now, func=update_seattle_lots, interval=120, result_ttl=240, repeat=None)
    scheduler.schedule(scheduled_time=now, func=update_free_spaces, interval=300, result_ttl=600, repeat=None)
    if not debug:
        scheduler.schedule(scheduled_time=now, func=hello_amazon, interval=300, result_ttl=600, repeat=None)
        scheduler.schedule(scheduled_time=now, func=send_notifications, interval=300, result_ttl=600, repeat=None)
        #scheduler.schedule(scheduled_time=now, func=update_deneigement, interval=1800, result_ttl=3600, repeat=None)

def stop_tasks():
    for x in scheduler.get_jobs():
        scheduler.cancel(x)

def run_backup(username, database):
    backup_dir = os.path.join(os.path.dirname(os.environ["PRKNG_SETTINGS"]), 'backup')
    file_name = 'prkng-{}.sql.gz'.format(datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    if not os.path.exists(backup_dir):
        os.mkdir(backup_dir)
    check_call('pg_dump -c -U {PG_USERNAME} {PG_DATABASE} | gzip > {}'.format(os.path.join(backup_dir, file_name),
        PG_USERNAME=username, PG_DATABASE=database),
        shell=True)
    return os.path.join(backup_dir, file_name)

def send_notifications():
    """
    Send a push notification to specified user IDs via Amazon SNS
    """
    CONFIG = create_app().config
    r = Redis(db=1)
    amz = boto.sns.connect_to_region("us-west-2",
        aws_access_key_id=CONFIG["AWS_ACCESS_KEY"],
        aws_secret_access_key=CONFIG["AWS_SECRET_KEY"])

    keys = r.hkeys('prkng:push')
    if not keys:
        return

    for pid in keys:
        message = r.hget('prkng:push', pid)
        r.hdel('prkng:push', pid)
        device_ids = r.lrange('prkng:push:'+pid, 0, -1)
        r.delete('prkng:push:'+pid)

        message_structure = None
        if message.startswith("{") and message.endswith("}"):
            message_structure = "json"
        mg_title = "message-group-{}".format(datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        mg_arn = None

        if device_ids == ["all"]:
            # Automatically publish messages destined for "all" via our All Users notification topic
            amz.publish(message=message, message_structure=message_structure,
                target_arn=CONFIG["AWS_SNS_TOPICS"]["all_users"])
        elif device_ids == ["ios"]:
            # Automatically publish messages destined for all iOS users
            amz.publish(message=message, message_structure=message_structure,
                target_arn=CONFIG["AWS_SNS_TOPICS"]["ios_users"])
        elif device_ids == ["android"]:
            # Automatically publish messages destined for all Android users
            amz.publish(message=message, message_structure=message_structure,
                target_arn=CONFIG["AWS_SNS_TOPICS"]["android_users"])
        elif device_ids == ["en"]:
            # Automatically publish messages destined for all English-language users
            amz.publish(message=message, message_structure=message_structure,
                target_arn=CONFIG["AWS_SNS_TOPICS"]["en_users"])
        elif device_ids == ["fr"]:
            # Automatically publish messages destined for all French-language users
            amz.publish(message=message, message_structure=message_structure,
                target_arn=CONFIG["AWS_SNS_TOPICS"]["fr_users"])

        if len(device_ids) >= 10:
            # If more than 10 real device IDs at once:
            for id in device_ids:
                if id.startswith("arn:aws:sns") and "endpoint" in id:
                    # this is a user device ID
                    # Create a temporary topic for a manually specified list of users
                    if not mg_arn:
                        mg_arn = amz.create_topic(mg_title)
                    try:
                        amz.subscribe(mg_arn, "application", id)
                    except:
                        pass
                elif id.startswith("arn:aws:sns"):
                    # this must be a topic ARN, send to it immediately
                    amz.publish(message=message, message_structure=message_structure, target_arn=id)
            if mg_arn:
                # send to all user device IDs that we queued up in the prior loop
                amz.publish(message=message, message_structure=message_structure, target_arn=mg_arn)
        else:
            # Less than 10 device IDs or topic ARNs. Send to them immediately
            for id in [x for x in device_ids if x.startswith("arn:aws:sns")]:
                amz.publish(message=message, message_structure=message_structure, target_arn=id)


def hello_amazon():
    """
    Fetch newly-registered users' device IDs and register with Amazon SNS for push notifications.
    """
    CONFIG = create_app().config
    db = PostgresWrapper(
        "host='{PG_HOST}' port={PG_PORT} dbname={PG_DATABASE} "
        "user={PG_USERNAME} password={PG_PASSWORD} ".format(**CONFIG))
    r = Redis(db=1)
    amz = boto.sns.connect_to_region("us-west-2",
        aws_access_key_id=CONFIG["AWS_ACCESS_KEY"],
        aws_secret_access_key=CONFIG["AWS_SECRET_KEY"])
    values = []

    # register the user's device ID with Amazon, and add to the "All Users" notification topic
    for d in ["ios", "android"]:
        for x in r.hkeys('prkng:hello-amazon:'+d):
            try:
                device_id = r.hget('prkng:hello-amazon:'+d, x)
                arn = amz.create_platform_endpoint(CONFIG["AWS_SNS_APPS"][d], device_id, x.encode('utf-8'))
                arn = arn['CreatePlatformEndpointResponse']['CreatePlatformEndpointResult']['EndpointArn']
                values.append("({},'{}')".format(x, arn))
                r.hdel('prkng:hello-amazon:'+d, x)
                amz.subscribe(CONFIG["AWS_SNS_TOPICS"]["all_users"], "application", arn)
                amz.subscribe(CONFIG["AWS_SNS_TOPICS"][d+"_users"], "application", arn)
            except Exception, e:
                if "already exists with the same Token" in e.message:
                    arn = re.search("Endpoint (arn:aws:sns\S*)\s.?", e.message)
                    if not arn:
                        continue
                    values.append("({},'{}')".format(x, arn.group(1)))
                    r.hdel('prkng:hello-amazon:'+d, x)

    # Update the local user records with their new Amazon SNS ARNs
    if values:
        db.query("""
            UPDATE users u SET sns_id = d.arn
            FROM (VALUES {}) AS d(uid, arn)
            WHERE u.id = d.uid
        """.format(",".join(values)))


def update_car2go():
    """
    Task to check with the car2go API, find moved cars and update their positions/slots
    """
    CONFIG = create_app().config
    db = PostgresWrapper(
        "host='{PG_HOST}' port={PG_PORT} dbname={PG_DATABASE} "
        "user={PG_USERNAME} password={PG_PASSWORD} ".format(**CONFIG))

    for city in ["montreal", "newyork", "seattle"]:
        # grab data from car2go api
        c2city = city
        if c2city == "newyork":
            c2city = "newyorkcity"
        raw = requests.get("https://www.car2go.com/api/v2.1/vehicles",
            params={"loc": c2city, "format": "json", "oauth_consumer_key": CONFIG["CAR2GO_CONSUMER"]})
        data = raw.json()["placemarks"]

        raw = requests.get("https://www.car2go.com/api/v2.1/parkingspots",
            params={"loc": c2city, "format": "json", "oauth_consumer_key": CONFIG["CAR2GO_CONSUMER"]})
        lot_data = raw.json()["placemarks"]

        # create or update car2go parking lots
        values = ["('{}','{}',{},{})".format(city, x["name"].replace("'", "''").encode("utf-8"),
            x["totalCapacity"], (x["totalCapacity"] - x["usedCapacity"])) for x in lot_data]
        if values:
            db.query("""
                UPDATE carshare_lots l SET capacity = d.capacity, available = d.available
                FROM (VALUES {}) AS d(city, name, capacity, available)
                WHERE l.company = 'car2go' AND l.city = d.city AND l.name = d.name
                    AND l.available != d.available
            """.format(",".join(values)))

        values = ["('{}','{}',{},{},'SRID=4326;POINT({} {})'::geometry)".format(city,
            x["name"].replace("'", "''").encode("utf-8"), x["totalCapacity"],
            (x["totalCapacity"] - x["usedCapacity"]), x["coordinates"][0],
            x["coordinates"][1]) for x in lot_data]
        if values:
            db.query("""
                INSERT INTO carshare_lots (company, city, name, capacity, available, geom, geojson)
                    SELECT 'car2go', d.city, d.name, d.capacity, d.available,
                            ST_Transform(d.geom, 3857), ST_AsGeoJSON(d.geom)::jsonb
                    FROM (VALUES {}) AS d(city, name, capacity, available, geom)
                    WHERE (SELECT 1 FROM carshare_lots l WHERE l.city = d.city AND l.name = d.name LIMIT 1) IS NULL
            """.format(",".join(values)))

        # unpark stale entries in our database
        db.query("""
            UPDATE carshares c SET since = NOW(), parked = false
            WHERE c.company = 'car2go'
                AND c.city = '{city}'
                AND c.parked = true
                AND (SELECT 1 FROM (VALUES {data}) AS d(pid) WHERE c.vin = d.pid LIMIT 1) IS NULL
        """.format(city=city, data=",".join(["('{}')".format(x["vin"]) for x in data])))

        # create or update car2go tracking with new data
        values = ["('{}','{}','{}','{}',{},'SRID=4326;POINT({} {})'::geometry)".format(city, x["vin"],
            x["name"].encode('utf-8'), x["address"].replace("'", "''").encode("utf-8"),
            x.get("fuel", 0), x["coordinates"][0], x["coordinates"][1]) for x in data]
        db.query("""
            WITH tmp AS (
                SELECT DISTINCT ON (d.vin) d.vin, d.name, d.fuel, d.address, d.geom,
                    s.id AS slot_id, l.id AS lot_id
                FROM (VALUES {}) AS d(city, vin, name, address, fuel, geom)
                LEFT JOIN carshare_lots l ON d.city = l.city AND l.name = d.address
                LEFT JOIN slots s ON l.id IS NULL AND d.city = s.city
                    AND ST_DWithin(ST_Transform(d.geom, 3857), s.geom, 5)
                ORDER BY d.vin, ST_Distance(ST_Transform(d.geom, 3857), s.geom)
            )
            UPDATE carshares c SET since = NOW(), name = t.name, address = t.address,
                parked = true, slot_id = t.slot_id, lot_id = t.lot_id, fuel = t.fuel,
                geom = ST_Transform(t.geom, 3857), geojson = ST_AsGeoJSON(t.geom)::jsonb
            FROM tmp t
            WHERE c.company = 'car2go'
                AND c.vin = t.vin
                AND c.parked = false
        """.format(",".join(values)))
        db.query("""
            INSERT INTO carshares (company, city, vin, name, address, slot_id, lot_id, parked, fuel, geom, geojson)
                SELECT DISTINCT ON (d.vin) 'car2go', d.city, d.vin, d.name, d.address, s.id, l.id,
                    true, d.fuel, ST_Transform(d.geom, 3857), ST_AsGeoJSON(d.geom)::jsonb
                FROM (VALUES {}) AS d(city, vin, name, address, fuel, geom)
                LEFT JOIN carshare_lots l ON d.city = l.city AND l.name = d.address
                LEFT JOIN slots s ON l.id IS NULL AND s.city = d.city
                    AND ST_DWithin(ST_Transform(d.geom, 3857), s.geom, 5)
                WHERE (SELECT 1 FROM carshares c WHERE c.vin = d.vin LIMIT 1) IS NULL
                ORDER BY d.vin, ST_Distance(ST_Transform(d.geom, 3857), s.geom)
        """.format(",".join(values)))


def update_automobile():
    """
    Task to check with the Auto-mobile API, find moved cars and update their positions/slots
    """
    CONFIG = create_app().config
    db = PostgresWrapper(
        "host='{PG_HOST}' port={PG_PORT} dbname={PG_DATABASE} "
        "user={PG_USERNAME} password={PG_PASSWORD} ".format(**CONFIG))

    # grab data from Auto-mobile api
    data = requests.get("https://www.reservauto.net/WCF/LSI/LSIBookingService.asmx/GetVehicleProposals",
        params={"Longitude": "-73.56307727766432", "Latitude": "45.48420949674474", "CustomerID": '""'})
    data = demjson.decode(data.text.lstrip("(").rstrip(");"))["Vehicules"]

    # unpark stale entries in our database
    if data:
        db.query("""
            UPDATE carshares c SET since = NOW(), parked = false
            WHERE c.company = 'auto-mobile'
                AND c.parked = true
                AND (SELECT 1 FROM (VALUES {data}) AS d(pid) WHERE c.vin = d.pid LIMIT 1) IS NULL
        """.format(data=",".join(["('{}')".format(x["Id"]) for x in data])))

        # create or update Auto-mobile tracking with newly parked vehicles
        values = ["('{}','{}',{},{},'{}','SRID=4326;POINT({} {})'::geometry)".format(x["Id"],
            x["Immat"].encode('utf-8'), x["EnergyLevel"], ("true" if x["Name"].endswith("-R") else "false"),
            x["Name"].encode('utf-8'), x["Position"]["Lon"], x["Position"]["Lat"]) for x in data]
        db.query("""
            WITH tmp AS (
                SELECT DISTINCT ON (d.vin) d.vin, d.name, d.fuel, d.id, s.id AS slot_id, s.way_name, d.geom
                FROM (VALUES {}) AS d(vin, name, fuel, electric, id, geom)
                JOIN cities c ON ST_Intersects(ST_Transform(d.geom, 3857), c.geom)
                LEFT JOIN slots s ON s.city = c.name
                    AND ST_DWithin(ST_Transform(d.geom, 3857), s.geom, 5)
                ORDER BY d.vin, ST_Distance(ST_Transform(d.geom, 3857), s.geom)
            )
            UPDATE carshares c SET partner_id = t.id, since = NOW(), name = t.name, address = t.way_name,
                parked = true, slot_id = t.slot_id, fuel = t.fuel, geom = ST_Transform(t.geom, 3857),
                geojson = ST_AsGeoJSON(t.geom)::jsonb
            FROM tmp t
            WHERE c.company = 'auto-mobile'
                AND c.vin = t.vin
                AND c.parked = false
        """.format(",".join(values)))
        db.query("""
            INSERT INTO carshares (company, city, partner_id, vin, name, address, slot_id, parked, fuel, electric, geom, geojson)
                SELECT DISTINCT ON (d.vin) 'auto-mobile', c.name, d.id, d.vin, d.name, s.way_name, s.id,
                    true, d.fuel, d.electric, ST_Transform(d.geom, 3857), ST_AsGeoJSON(d.geom)::jsonb
                FROM (VALUES {}) AS d(vin, name, fuel, electric, id, geom)
                JOIN cities c ON ST_Intersects(ST_Transform(d.geom, 3857), c.geom)
                LEFT JOIN slots s ON s.city = c.name
                    AND ST_DWithin(ST_Transform(d.geom, 3857), s.geom, 5)
                WHERE (SELECT 1 FROM carshares c WHERE c.vin = d.vin LIMIT 1) IS NULL
                ORDER BY d.vin, ST_Distance(ST_Transform(d.geom, 3857), s.geom)
        """.format(",".join(values)))


def update_communauto():
    """
    Task to check with the Communuauto API, find moved cars and update their positions/slots
    """
    CONFIG = create_app().config
    db = PostgresWrapper(
        "host='{PG_HOST}' port={PG_PORT} dbname={PG_DATABASE} "
        "user={PG_USERNAME} password={PG_PASSWORD} ".format(**CONFIG))

    for city in ["montreal", "quebec"]:
        # grab data from communauto api
        if city == "montreal":
            cacity = 59
        elif city == "quebec":
            cacity = 90
        start = datetime.datetime.utcnow().replace(tzinfo=pytz.utc).astimezone(pytz.timezone('US/Eastern'))
        finish = (start + datetime.timedelta(minutes=30))
        data = requests.post("https://www.reservauto.net/Scripts/Client/Ajax/PublicCall/Get_Car_DisponibilityJSON.asp",
            data={"CityID": cacity, "StartDate": start.strftime("%d/%m/%Y %H:%M"),
                "EndDate": finish.strftime("%d/%m/%Y %H:%M"), "FeeType": 80})
        # must use demjson here because returning format is non-standard JSON
        try:
            data = demjson.decode(data.text.lstrip("(").rstrip(")"))["data"]
        except:
            return

        # create or update communauto parking spaces
        values = ["('{}',{})".format(x["StationID"], (1 if x["NbrRes"] == 0 else 0)) for x in data]
        db.query("""
            UPDATE carshare_lots l SET capacity = 1, available = d.available
            FROM (VALUES {}) AS d(pid, available)
            WHERE l.company = 'communauto'
                AND l.partner_id = d.pid
                AND l.available != d.available
        """.format(",".join(values)))

        values = ["('{}','{}',{},'{}','SRID=4326;POINT({} {})'::geometry)".format(city,
            x["strNomStation"].replace("'", "''").encode("utf-8"), (1 if x["NbrRes"] == 0 else 0),
            x["StationID"], x["Longitude"], x["Latitude"]) for x in data]
        db.query("""
            INSERT INTO carshare_lots (company, city, name, capacity, available, partner_id, geom, geojson)
                SELECT 'communauto', d.city, d.name, 1, d.available, d.partner_id,
                        ST_Transform(d.geom, 3857), ST_AsGeoJSON(d.geom)::jsonb
                FROM (VALUES {}) AS d(city, name, available, partner_id, geom)
                WHERE (SELECT 1 FROM carshare_lots l WHERE l.partner_id = d.partner_id LIMIT 1) IS NULL
        """.format(",".join(values)))

        # unpark stale entries in our database
        db.query("""
            UPDATE carshares c SET since = NOW(), parked = false
            FROM (VALUES {data}) AS d(pid, lot_id, numres)
            WHERE c.parked = true
                AND c.city = '{city}'
                AND d.numres = 1
                AND c.company = 'communauto'
                AND c.partner_id = d.pid;

            UPDATE carshares c SET since = NOW(), parked = false
            WHERE c.parked = true
                AND c.company = 'communauto'
                AND c.city = '{city}'
                AND (SELECT 1 FROM (VALUES {data}) AS d(pid, lot_id, numres) WHERE d.pid != c.partner_id
                     AND d.lot_id = c.lot_id LIMIT 1) IS NOT NULL
        """.format(city=city, data=",".join(["('{}',{},{})".format(x["CarID"],x["StationID"],x["NbrRes"]) for x in data])))

        # create or update communauto tracking with newly parked vehicles
        values = ["('{}',{},'{}','{}','{}'::timestamp,'SRID=4326;POINT({} {})'::geometry)".format(x["CarID"],
            x["NbrRes"], x["Model"].encode("utf-8"), x["strNomStation"].replace("'", "''").encode("utf-8"),
            x["AvailableUntilDate"] or "NOW", x["Longitude"], x["Latitude"]) for x in data]
        db.query("""
            UPDATE carshares c SET since = NOW(), until = d.until, name = d.name, address = d.address,
                parked = true, geom = ST_Transform(d.geom, 3857), geojson = ST_AsGeoJSON(d.geom)::jsonb
            FROM (VALUES {}) AS d(pid, numres, name, address, until, geom)
            WHERE c.company = 'communauto'
                AND c.partner_id = d.pid
                AND d.numres = 0
        """.format(",".join(values)))

        values = ["('{}','{}','{}','{}','{}',{},'{}'::timestamp,'SRID=4326;POINT({} {})'::geometry)".format(city,
            x["StationID"], x["CarID"], x["Model"].encode("utf-8"), x["strNomStation"].replace("'", "''").encode("utf-8"),
            x["NbrRes"], x["AvailableUntilDate"] or "NOW", x["Longitude"], x["Latitude"]) for x in data]
        db.query("""
            INSERT INTO carshares (company, city, partner_id, name, address, lot_id, parked, until, geom, geojson)
                SELECT 'communauto', d.city, d.partner_id, d.name, d.address, l.id, d.numres = 0,
                        d.until, ST_Transform(d.geom, 3857), ST_AsGeoJSON(d.geom)::jsonb
                FROM (VALUES {}) AS d(city, lot_pid, partner_id, name, address, numres, until, geom)
                JOIN carshare_lots l ON l.company = 'communauto' AND l.city = d.city
                    AND l.partner_id = d.lot_pid
                WHERE (SELECT 1 FROM carshares c WHERE c.partner_id = d.partner_id LIMIT 1) IS NULL
        """.format(",".join(values)))


def update_zipcar():
    """
    Task to check with the Zipcar API and update parking lot data
    """
    CONFIG = create_app().config
    db = PostgresWrapper(
        "host='{PG_HOST}' port={PG_PORT} dbname={PG_DATABASE} "
        "user={PG_USERNAME} password={PG_PASSWORD} ".format(**CONFIG))

    lots, cars, vids = [], [], []
    raw = requests.get("https://api.zipcar.com/partner-api/directory",
        params={"country": "us", "embed": "vehicles", "apikey": CONFIG["ZIPCAR_KEY"]})
    data = raw.json()["locations"]
    for x in data:
        if not x["address"]["city"] or not x["address"]["city"] \
                in ["Seattle", "New York", "Brooklyn", "Queens", "Staten Island"]:
            continue
        city = x["address"]["city"].encode("utf-8").lower()
        if x["address"]["city"] in ["New York", "Brooklyn", "Queens", "Staten Island"]:
            city = "newyork"
        lots.append("('{}','{}','{}',{},'SRID=4326;POINT({} {})'::geometry)".format(
            x["location_id"], city, x["display_name"].replace("'", "''").encode("utf-8"),
            len(x["vehicles"]), x["coordinates"]["lng"], x["coordinates"]["lat"]
        ))
        for y in x["vehicles"]:
            cars.append("('{}','{}','{}','{}','{}','SRID=4326;POINT({} {})'::geometry)".format(
                y["vehicle_id"], y["vehicle_name"].replace("'", "''").encode("utf-8"),
                city, x["address"]["street"].replace("'", "''").encode("utf-8"),
                x["location_id"], x["coordinates"]["lng"], x["coordinates"]["lat"]
            ))
            vids.append(y["vehicle_id"])

    if lots:
        db.query("""
            UPDATE carshare_lots l SET name = d.name, capacity = d.capacity, available = d.capacity
            FROM (VALUES {}) AS d(pid, city, name, capacity, geom)
            WHERE l.company = 'zipcar'
                AND l.partner_id = d.pid
                AND (l.available != d.capacity OR l.capacity != d.capacity OR l.name != d.name)
        """.format(",".join(lots)))
        db.query("""
            INSERT INTO carshare_lots (company, partner_id, city, name, capacity, available, geom, geojson)
            SELECT 'zipcar', d.pid, d.city, d.name, d.capacity, d.capacity,
                    ST_Transform(d.geom, 3857), ST_AsGeoJSON(d.geom)::jsonb
            FROM (VALUES {}) AS d(pid, city, name, capacity, geom)
            WHERE (SELECT 1 FROM carshare_lots l WHERE l.city = d.city AND l.partner_id = d.pid LIMIT 1) IS NULL
        """.format(",".join(lots)))
    if cars:
        db.query("""
            INSERT INTO carshares (company, city, partner_id, name, address, lot_id, parked, geom, geojson)
                SELECT 'zipcar', d.city, d.pid, d.name, d.address, l.id, true,
                        ST_Transform(d.geom, 3857), ST_AsGeoJSON(d.geom)::jsonb
                FROM (VALUES {}) AS d(pid, name, city, address, lot_pid, geom)
                JOIN carshare_lots l ON l.company = 'zipcar' AND l.city = d.city
                    AND l.partner_id = d.lot_pid
                WHERE (SELECT 1 FROM carshares c WHERE c.partner_id = d.pid LIMIT 1) IS NULL
        """.format(",".join(cars)))
    db.query("""
        DELETE FROM carshare_lots l
        WHERE l.company = 'zipcar'
            AND (SELECT 1 FROM (VALUES {}) AS d(pid) WHERE l.company = 'zipcar' AND l.partner_id = d.pid) IS NULL
    """.format(",".join(["('{}')".format(z["location_id"]) for z in data])))
    db.query("""
        DELETE FROM carshares l
        WHERE l.company = 'zipcar'
            AND (SELECT 1 FROM (VALUES {}) AS d(pid) WHERE l.company = 'zipcar' AND l.partner_id = d.pid) IS NULL
    """.format(",".join(["('{}')".format(z) for z in vids])))


def update_parkingpanda():
    """
    Task to check with the Parking Panda API, update data on associated parking lots
    """
    CONFIG = create_app().config
    db = PostgresWrapper(
        "host='{PG_HOST}' port={PG_PORT} dbname={PG_DATABASE} "
        "user={PG_USERNAME} password={PG_PASSWORD} ".format(**CONFIG))

    for city, addr in [("newyork", "4 Pennsylvania Plaza, New York, NY")]:
        # grab data from parkingpanda api
        start = datetime.datetime.utcnow().replace(tzinfo=pytz.utc).astimezone(pytz.timezone('US/Eastern'))
        finish = (start + datetime.timedelta(hours=23, minutes=59))
        data = requests.get("https://www.parkingpanda.com/api/v2/locations",
            params={"search": addr, "miles": 20.0, "startDate": start.strftime("%m/%d/%Y"),
                "startTime": start.strftime("%H:%M"), "endDate": finish.strftime("%m/%d/%Y"),
                "endTime": finish.strftime("%H:%M"), "onlyavailable": False,
                "showSoldOut": True, "peer": False})
        data = data.json()["data"]["locations"]

        hourToFloat = lambda x: float(x.split(":")[0]) + (float(x.split(":")[1][0:2]) / 60) + (12 if "PM" in x and x.split(":")[0] != "12" else 0)
        values = []
        for x in data:
            x["displayName"] = x["displayName"].replace("'","''").encode("utf-8")
            x["displayAddress"] = x["displayAddress"].replace("'","''").encode("utf-8")
            x["description"] = x["description"].replace("'","''").encode("utf-8")
            basic = "('{}','{}','{}',{},{},'{}','{}','SRID=4326;POINT({} {})'::geometry,'{}'::jsonb,'{}'::jsonb)"
            if x["isOpen247"]:
                agenda = {str(y): [{"max": None, "hourly": None, "daily": x["price"],
                    "hours": [0.0,24.0]}] for y in range(1,8)}
            else:
                agenda = {str(y): [] for y in range(1,8)}
                for y in x["hoursOfOperation"]:
                    if not y["isOpen"]:
                        continue
                    hours = [hourToFloat(y["timeOfDayOpen"]), hourToFloat(y["timeOfDayClose"])]
                    if y["timeOfDayClose"] == "11:59 PM":
                        hours[1] = 24.0
                    if hours != [0.0, 24.0] and hours[0] > hours[1]:
                        nextday = str(y["dayOfWeek"]+2) if (y["dayOfWeek"] < 6) else "1"
                        agenda[nextday].append({"max": None, "hourly": None,
                            "daily": x["price"], "hours": [0.0, hours[1]]})
                        hours = [hours[0], 24.0]
                    agenda[str(y["dayOfWeek"]+1)].append({"max": None, "hourly": None,
                        "daily": x["price"], "hours": hours})
            # Create "closed" rules for periods not covered by an open rule
            for j in agenda:
                hours = sorted([y["hours"] for y in agenda[j]], key=lambda z: z[0])
                for i, y in enumerate(hours):
                    starts = [z[0] for z in hours]
                    if y[0] == 0.0:
                        continue
                    last_end = hours[i-1][1] if not i == 0 else 0.0
                    next_start = hours[i+1][0] if not i == (len(hours) - 1) else 24.0
                    if not last_end in starts:
                        agenda[j].append({"hours": [last_end, y[0]], "hourly": None, "max": None,
                            "daily": None})
                    if not next_start in starts and y[1] != 24.0:
                        agenda[j].append({"hours": [y[1], next_start], "hourly": None, "max": None,
                            "daily": None})
                if agenda[j] == []:
                    agenda[j].append({"hours": [0.0,24.0], "hourly": None, "max": None, "daily": None})
            attrs = {"card": True, "indoor": "covered" in [y["name"] for y in x["amenities"]],
                "handicap": "accessible" in [y["name"] for y in x["amenities"]],
                "valet": "valet" in [y["name"] for y in x["amenities"]]}
            values.append(basic.format(x["id"], city, x["displayName"], json.dumps(x["isLive"]),
                x["availableSpaces"], x["displayAddress"], x["description"],
                x["longitude"], x["latitude"], json.dumps(agenda), json.dumps(attrs)))

        if values:
            db.query("""
                UPDATE parking_lots l SET available = d.available, agenda = d.agenda, attrs = d.attrs,
                    active = d.active
                FROM (VALUES {}) AS d(pid, city, name, active, available, address, description,
                    geom, agenda, attrs)
                WHERE l.partner_name = 'Parking Panda'
                    AND l.partner_id = d.pid
            """.format(",".join(values)))
            db.query("""
                INSERT INTO parking_lots (partner_id, partner_name, city, name, active,
                    available, address, description, geom, geojson, agenda, attrs, street_view)
                SELECT d.pid, 'Parking Panda', d.city, d.name, d.active, d.available, d.address,
                    d.description, ST_Transform(d.geom, 3857), ST_AsGeoJSON(d.geom)::jsonb,
                    d.agenda, d.attrs, json_build_object('head', p.street_view_head, 'id', p.street_view_id)::jsonb
                FROM (VALUES {}) AS d(pid, city, name, active, available, address, description,
                    geom, agenda, attrs)
                LEFT JOIN parking_lots_streetview p ON p.partner_name = 'Parking Panda' AND p.partner_id = d.pid
                WHERE (SELECT 1 FROM parking_lots l WHERE l.partner_id = d.pid LIMIT 1) IS NULL
            """.format(",".join(values)))


def update_seattle_lots():
    """
    Fetch Seattle parking lot data and real-time availability from City of Seattle GIS
    """
    CONFIG = create_app().config
    db = PostgresWrapper(
        "host='{PG_HOST}' port={PG_PORT} dbname={PG_DATABASE} "
        "user={PG_USERNAME} password={PG_PASSWORD} ".format(**CONFIG))

    # grab data from city of seattle dot
    data = requests.get("http://web6.seattle.gov/sdot/wsvcEparkGarageOccupancy/Occupancy.asmx/GetGarageList",
        params={"prmGarageID": "G", "prmMyCallbackFunctionName": ""})
    data = json.loads(data.text.lstrip("(").rstrip(");"))

    if data:
        db.query("""
            UPDATE parking_lots l SET available = d.available
            FROM (VALUES {}) AS d(pid, available)
            WHERE l.partner_name = 'Seattle ePark'
                AND l.partner_id = d.pid
        """.format(",".join(["('{}',{})".format(x["Id"], x["VacantSpaces"]) for x in data])))


def update_free_spaces():
    """
    Task to check recently departed carshare spaces and record
    """
    CONFIG = create_app().config
    db = PostgresWrapper(
        "host='{PG_HOST}' port={PG_PORT} dbname={PG_DATABASE} "
        "user={PG_USERNAME} password={PG_PASSWORD} ".format(**CONFIG))

    start = datetime.datetime.now()
    finish = start - datetime.timedelta(minutes=5)

    db.query("""
        INSERT INTO free_spaces (slot_ids)
          SELECT array_agg(s.id) FROM slots s
            JOIN carshares c ON c.slot_id = s.id
            WHERE c.lot_id IS NULL
              AND c.parked = false
              AND c.since  > '{}'
              AND c.since  < '{}'
    """.format(finish.strftime('%Y-%m-%d %H:%M:%S'), start.strftime('%Y-%m-%d %H:%M:%S')))


def update_deneigement():
    """
    Task to check with Montreal Planif-Neige API and note snow-clearing operations
    """
    CONFIG = create_app().config
    db = PostgresWrapper(
        "host='{PG_HOST}' port={PG_PORT} dbname={PG_DATABASE} "
        "user={PG_USERNAME} password={PG_PASSWORD} ".format(**CONFIG))

    client = Client("https://servicesenligne2.ville.montreal.qc.ca/api/infoneige/sim/InfoneigeWebService?wsdl")
    planification_request = client.factory.create('getPlanificationsForDate')
    planification_request.fromDate = (datetime.datetime.now() - datetime.timedelta(minutes=30)).strftime('%Y-%m-%dT%H:%M:%S')
    planification_request.tokenString = CONFIG["PLANIFNEIGE_SIM_KEY"]
    response = client.service.GetPlanificationsForDate(planification_request)
    if response['responseStatus'] == 8:
        # No new data
        return
    elif response['responseStatus'] != 0:
        # An error occurred
        return
    db.query("""
        CREATE TABLE IF NOT EXISTS temporary_restrictions (
            id serial primary key,
            city varchar,
            partner_id varchar,
            slot_ids integer[],
            start timestamp,
            finish timestamp,
            type varchar,
            rule jsonb,
            active boolean
        )
    """)
    values, record = [], "({},'{}'::timestamp,'{}'::timestamp,{},'{}'::jsonb,{})"
    for x in response['planifications']['planification']:
        if x['etatDeneig'] in [2, 3] and hasattr(x, 'dateDebutPlanif'):
            agenda = {str(z): [] for z in range(1,8)}
            debutJour, finJour = x['dateDebutPlanif'].isoweekday(), x['dateFinPlanif'].isoweekday()
            debutHeure = float(x['dateDebutPlanif'].hour) + (float(x['dateDebutPlanif'].minute) / 60.0)
            finHeure = float(x['dateFinPlanif'].hour) + (float(x['dateFinPlanif'].minute) / 60.0)
            if debutJour == finJour:
                agenda[str(debutJour)] = [[debutHeure, finHeure]]
            else:
                agenda[str(debutJour)] = [[debutHeure, 24.0]]
                agenda[str(finJour)] = [[0.0, finHeure]]
                if (x['dateFinPlanif'].day - x['dateDebutPlanif'].day) > 1:
                    if debutJour > finJour:
                        for z in range(debutJour, 8):
                            agenda[str(z)] = [[0.0,24.0]]
                        for z in range(1, finJour + 1):
                            agenda[str(z)] = [[0.0,24.0]]
                    else:
                        for z in range(debutJour + 1, finJour + 1):
                            agenda[str(z)] = [[0.0,24.0]]
            rule = {"code": "MTL-NEIGE", "description": "DÉNEIGEMENT PRÉVU DANS CE SECTEUR",
                "season_start": None, "season_end": None, "agenda": agenda, "time_max_parking": None,
                "special_days": None, "restrict_types": ["snow"], "permit_no": None}
            values.append(record.format(x['coteRueId'], x['dateDebutPlanif'].strftime('%Y-%m-%d %H:%M:%S'),
                x['dateFinPlanif'].strftime('%Y-%m-%d %H:%M:%S'), 'true', json.dumps(rule), x['etatDeneig']))
        elif x['etatDeneig'] in [0, 1, 4]:
            values.append(record.format(x['coteRueId'], datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'false', '{}', x['etatDeneig']))

    if values:
        # update temporary restrictions item when we are already tracking the blockface
        db.query("""
            WITH tmp AS (
                SELECT x.*, g.name
                FROM (VALUES {}) AS x(geobase_id, start, finish, active, rule, state)
                JOIN montreal_geobase_double d ON x.geobase_id = d.cote_rue_i
                JOIN montreal_roads_geobase g ON d.id_trc = g.id_trc
            )
            UPDATE temporary_restrictions d SET start = x.start, finish = x.finish,
                active = x.active, rule = x.rule
            FROM tmp x
            WHERE d.city = 'montreal' AND d.type = 'snow' AND x.geobase_id::text = d.partner_id
        """.format(",".join(values)))

        # insert temporary restrictions for newly-mentioned blockfaces, and link with current slot IDs
        db.query("""
            WITH tmp AS (
                SELECT DISTINCT ON (d.cote_rue_i) d.cote_rue_i AS id,
                    array_agg(s.id) AS slot_ids
                FROM montreal_geobase_double d
                JOIN montreal_roads_geobase g ON d.id_trc = g.id_trc
                JOIN slots s ON city = 'montreal' AND s.rid = g.id
                    AND ST_isLeft(g.geom, ST_StartPoint(ST_LineMerge(d.geom)))
                      = ST_isLeft(g.geom, ST_StartPoint(s.geom))
                GROUP BY d.cote_rue_i
            )
            INSERT INTO temporary_restrictions (city, partner_id, slot_ids, start, finish,
                    rule, type, active)
                SELECT 'montreal', x.geobase_id::text, t.slot_ids, min(x.start), min(x.finish),
                    x.rule, 'snow', x.active
                FROM (VALUES {}) AS x(geobase_id, start, finish, active, rule, state)
                JOIN tmp t ON t.id = x.geobase_id
                WHERE (SELECT 1 FROM temporary_restrictions l WHERE l.type = 'snow'
                            AND l.partner_id = x.geobase_id::text LIMIT 1) IS NULL
        """.format(",".join(values)))

        # grab the appropriate checkins to send pushes to by slot ID
        res = db.query("""
            SELECT x.start, x.state, u.sns_id
            FROM (VALUES {}) AS x(geobase_id, start, finish, active, rule, state)
            JOIN montreal_geobase_double d ON x.geobase_id = d.cote_rue_i
            JOIN montreal_roads_geobase g ON d.id_trc = g.id_trc
            JOIN slots s ON city = 'montreal' AND s.rid = g.id
                AND ST_isLeft(g.geom, ST_StartPoint(ST_LineMerge(d.geom)))
                  = ST_isLeft(g.geom, ST_StartPoint(s.geom))
            JOIN checkins c ON s.id = c.slot_id
            JOIN users u ON c.user_id = u.id
            WHERE c.active = true AND c.checkout_time IS NOT NULL
                AND c.push_notify = true AND u.sns_id IS NOT NULL
                AND (x.state = 2 OR x.state = 3)
        """).format(",".join(values))

        # group device IDs by schedule/reschedule and start time, then send messages
        scheduled, rescheduled = {}, {}
        for x in res:
            x = (x[0].isoformat(), x[1], x[2])
            if x[1] == 2:
                if not scheduled.has_key(x[0]):
                    scheduled[x[0]] = []
                scheduled[x[0]].append(x[2])
            elif x[1] == 3:
                if not rescheduled.has_key(x[0]):
                    rescheduled[x[0]] = []
                rescheduled[x[0]].append(x[2])
        for x in scheduled.keys():
            notifications.schedule_notifications(scheduled[x],
                json.dumps({"message_type": "snow_removal_scheduled", "data": {"start": x}}))
        for x in rescheduled.keys():
            notifications.schedule_notifications(rescheduled[x],
                json.dumps({"message_type": "snow_removal_rescheduled", "data": {"start": x}}))


def update_analytics():
    """
    Task to push analytics submissions from Redis to DB
    """
    CONFIG = create_app().config
    db = PostgresWrapper(
        "host='{PG_HOST}' port={PG_PORT} dbname={PG_DATABASE} "
        "user={PG_USERNAME} password={PG_PASSWORD} ".format(**CONFIG))
    r = Redis(db=1)

    data = r.lrange('prkng:analytics:pos', 0, -1)
    r.delete('prkng:analytics:pos')

    values = ["({}, {}, {}, '{}'::timestamp, '{}')".format(x["user_id"], x["lat"], x["long"],
        x["created"], x["search_type"]) for x in map(lambda y: json.loads(y), data)]
    if values:
        pos_query = """
            WITH tmp AS (
                SELECT
                    user_id,
                    search_type,
                    count(*),
                    date_trunc('hour', created) AS hour_stump,
                    (extract(minute FROM created)::int / 5) AS min_by5,
                    ST_Collect(ST_Transform(ST_SetSRID(ST_MakePoint(long, lat), 4326), 3857)) AS geom
                FROM (VALUES {}) AS d(user_id, lat, long, created, search_type)
                GROUP BY 1, 2, 4, 5
                ORDER BY 1, 2, 4, 5
            )
            INSERT INTO analytics_pos (user_id, geom, centerpoint, count, created, search_type)
                SELECT user_id, geom, ST_Centroid(geom), count, hour_stump + (INTERVAL '5 MINUTES' * min_by5),
                    search_type
                FROM tmp
        """.format(",".join(values))
        db.query(pos_query)

    data = r.lrange('prkng:analytics:event', 0, -1)
    r.delete('prkng:analytics:event')

    if data:
        event_query = "INSERT INTO analytics_event (user_id, lat, long, created, event) VALUES "
        event_query += ",".join(["({}, {}, {}, '{}', '{}')".format(x["user_id"], x["lat"] or "NULL",
            x["long"] or "NULL", x["created"], x["event"]) for x in map(lambda y: json.loads(y), data)])
        db.query(event_query)
