# -*- coding: utf-8 -*-
from itertools import chain
from datetime import timedelta, datetime
from aniso8601 import parse_datetime


def on_restriction(rules, checkin, duration, paid=True, permit=False):
    """
    Returns True if restrictions are consistent with the checkin
    and duration given in argument. False otherwise

    :param rules: list of rules (dict)
    :param checkin: checkin time
    :param duration: duration in hour. Float accepted
    """
    checkin = parse_datetime(checkin)
    duration = timedelta(hours=duration)
    checkin_end = checkin + duration  # datetime

    month = checkin.date().month  # month as number
    isodow = checkin.isoweekday()  # 1->7
    year = checkin.year  # 2015
    day = checkin.strftime('%d')  # 07

    # analyze each rule and stop iteration on conflict
    for rule in rules:
        if rule.get('restrict_typ') == 'paid' and not paid:
            # don't show me paid slots
            return True
        elif rule.get('restrict_typ') in ['paid', 'angled']:
            # not concerned, going to the next rule
            continue

        # first test season day/month
        start_month, start_day = ('-' or rule['season_start']).split('-')
        end_month, end_day = ('-' or rule['season_end']).split('-')
        season_match = season_matching(
            start_day,
            start_month,
            end_day,
            end_month,
            day,
            month
        )

        if not season_match:
            # not concerned, going to the next rule
            continue

        if rule.get('restrict_typ') == 'permit' and (permit == 'all' or str(permit) == str(rule.get('permit_no'))):
            # this is a permit rule and we like permits
            continue

        max_time_ok = True
        time_range_ok = True

        # analyze time_max_parking
        time_max_parking = timedelta(minutes=rule['time_max_parking'] or 3679200)

        # extract time range for each day and test overlapping with checkin + duration
        # start at current day and slice over days
        iterto = chain(range(1, 8)[isodow-1:], range(1, 8)[:isodow-1])

        for absoluteday, numday in enumerate(iterto):
            tsranges = rule['agenda'][str(numday)]
            for start, stop in filter(bool, tsranges):

                try:
                    start_time = datetime(year, month, int(day), hour=int(start), minute=int(start % 1 * 60)) \
                        + timedelta(days=absoluteday)
                    #  hack to avoid ValueError: hour must be in 0..23
                    stop_time = datetime(year, month, int(day), hour=int(stop-1), minute=int(stop % 1 * 60)) \
                        + timedelta(days=absoluteday, hours=1)
                except TypeError:
                    raise Exception("Data integrity error on {}, please review rules".format(rule['code']))
                except Exception, e:
                    raise Exception("Exception occurred on {} :  {}".format(rule['code'], str(e)))

                if (max(start_time, checkin) < min(stop_time, checkin_end) and rule['time_max_parking'] == None):
                    # overlapping !
                    time_range_ok &= False

                if (max(start_time, checkin) < min(stop_time, checkin_end) and rule['time_max_parking'] <> None):
                    # overlapping BUT a time_max_parking is allowed!
                    #if checkin_end time is after the stop time
                    if duration > time_max_parking and (checkin_end > stop_time):
                        #extract duration inside
                        duration_in_checkin_range = (stop_time - checkin)
                        if (duration_in_checkin_range > time_max_parking):
                            max_time_ok &= False

                    #if checkin time is before the start time
                    if duration > time_max_parking and (checkin < start_time):
                        #extract duration inside
                        duration_in_checkin_range = (checkin_end - start_time)
                        if (duration_in_checkin_range > time_max_parking):
                            max_time_ok &= False

                    if (checkin >= start_time and checkin_end <= stop_time and duration > time_max_parking):
                        # parking time is totally inside range BUT duration too long
                        max_time_ok &= False

            if not max_time_ok or not time_range_ok:
                # max_time exceed or time range overlapping or both
                return True

    return False


def assign_type(slot, checkin):
    slot['restrict_typ'] = None

    if not [(x.get("metered") or x["restrict_typ"] == "paid") for x in slot['rules']]:
        # simple, the slot has no paid rules
        # give it back as-is
        return feature

    checkin = parse_datetime(checkin) if checkin else datetime.now()
    month = checkin.date().month  # month as number
    isodow = checkin.isoweekday()  # 1->7
    year = checkin.year  # 2015
    day = checkin.strftime('%d')  # 07

    for rule in [x for x in slot['rules'] if (x.get("metered") or x["restrict_typ"] == "paid")]:
        # first test season day/month
        start_month, start_day = ('-' or rule['season_start']).split('-')
        end_month, end_day = ('-' or rule['season_end']).split('-')
        season_match = season_matching(
            start_day,
            start_month,
            end_day,
            end_month,
            day,
            month
        )

        if not season_match:
            # not concerned, going to the next rule
            continue

        # extract time range for each day and test overlapping with checkin + duration
        # start at current day and slice over days
        iterto = chain(range(1, 8)[isodow-1:], range(1, 8)[:isodow-1])

        for absoluteday, numday in enumerate(iterto):
            tsranges = rule['agenda'][str(numday)]
            for start, stop in filter(bool, tsranges):
                try:
                    start_time = datetime(year, month, int(day), hour=int(start), minute=int(start % 1 * 60)) \
                        + timedelta(days=absoluteday)
                    #  hack to avoid ValueError: hour must be in 0..23
                    stop_time = datetime(year, month, int(day), hour=int(stop-1), minute=int(stop % 1 * 60)) \
                        + timedelta(days=absoluteday, hours=1)
                except TypeError:
                    raise Exception("Data integrity error on {}, please review rules".format(rule['code']))
                except Exception, e:
                    raise Exception("Exception occurred on {} :  {}".format(rule['code'], str(e)))

                if start_time < checkin and stop_time > checkin:
                    # we are in the period, say we're paid and get out
                    slot['restrict_typ'] = "paid"
                    return slot
    return slot


def season_matching(start_day, start_month, end_day, end_month,
                    day, month):
    if not start_month:
        # no season restriction so matching ok
        return True

    if start_month < end_month and not (month >= start_month and month <= end_month):
        # month out of range
        return False
    if start_month > end_month and (month < start_month and month > end_month):
        # month out of range
        return False

    if month == start_month:
        if day < start_day:
            return False

    if month == end_month:
        if day > end_day:
            return False

    return True