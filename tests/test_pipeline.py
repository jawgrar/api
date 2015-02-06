# -*- coding: utf-8 -*-
from collections import namedtuple

import pytest

from prkng.processing.pipeline import split_time_range, group_rules
from prkng.filters import on_restriction
from prkng.filters import season_matching


@pytest.fixture(scope="module")
def rules():
    rule = namedtuple('rule', (
        'id', 'code', 'description', 'season_start', 'season_end',
        'time_max_parking', 'time_start', 'time_end', 'time_duration',
        'lun', 'mar', 'mer', 'jeu', 'ven', 'sam', 'dim', 'daily',
        'special_days', 'restrict_typ'
    ))

    return [
        rule(
            id=1919,
            code='PX-MN',
            description='PANONCEAU 1 MAI AU 1 NOV.',
            season_start='05-01',
            season_end='11-01',
            time_max_parking=None,
            time_start=None,
            time_end=None,
            time_duration=None,
            lun=None,
            mar=None,
            mer=None,
            jeu=None,
            ven=None,
            sam=None,
            dim=None,
            daily=1.0,
            special_days=None,
            restrict_typ=None
        ),
        rule(
            id=9,
            code='SLR-ST-105',
            description='8H À 12H MAR JEU 13H À 17H LUN MER VEN',
            season_start=None,
            season_end=None,
            time_max_parking=None,
            time_start=13.0,
            time_end=17.0,
            time_duration=4.0,
            lun=1,
            mar=None,
            mer=1,
            jeu=None,
            ven=1,
            sam=None,
            dim=None,
            daily=None,
            special_days=None,
            restrict_typ=None
        ),
        rule(
            id=10,
            code='SLR-ST-105',
            description='8H À 12H MAR JEU 13H À 17H LUN MER VEN',
            season_start=None,
            season_end=None,
            time_max_parking=None,
            time_start=8.0,
            time_end=12.0,
            time_duration=4.0,
            lun=None,
            mar=1,
            mer=None,
            jeu=1,
            ven=None,
            sam=None,
            dim=None,
            daily=None,
            special_days=None,
            restrict_typ=None
        ),
        rule(
            id=1672,
            code='OUT-SDX-10',
            description='\\P EXCEPTE 8h - 12h LUNDI 1er AVRIL - 30 NOV',
            season_start='04-01',
            season_end='11-30',
            time_max_parking=None,
            time_start=12.0,
            time_end=None,
            time_duration=164.0,
            lun=1,
            mar=None,
            mer=None,
            jeu=None,
            ven=None,
            sam=None,
            dim=None,
            daily=None,
            special_days=None,
            restrict_typ=None
        ),
        rule(
            id=443,
            code='SD-TT',
            description='\\P EN TOUT TEMPS',
            season_start=None,
            season_end=None,
            time_max_parking=None,
            time_start=0.0,
            time_end=None,
            time_duration=24.0,
            lun=None,
            mar=None,
            mer=None,
            jeu=None,
            ven=None,
            sam=None,
            dim=None,
            daily=1.0,
            special_days=None,
            restrict_typ=None
        )]


def test_split_time_range_oneday():
    assert split_time_range(16, 5) == (5, 0, 0)


def test_split_time_range_twodays():
    assert split_time_range(16, 10) == (8, 0, 2)
    assert split_time_range(16, 10.5) == (8, 0, 2.5)


def test_split_time_range_severaldays():
    """should receive 8 hours left on the first day, 3 plain days
    and 20 hours on the last day"""
    assert split_time_range(16, 100) == (8, 3, 20)


def test_grouping_rules_single(rules):
    res = group_rules(rules)
    days = filter(lambda x: x.code == 'PX-MN', res)[0].agenda

    assert days == {1: [[0, 24]], 2: [[0, 24]],
                    3: [[0, 24]], 4: [[0, 24]],
                    5: [[0, 24]], 6: [[0, 24]],
                    7: [[0, 24]]}


def test_grouping_rules_multi(rules):
    res = group_rules(rules)
    days = filter(lambda x: x.code == 'SLR-ST-105', res)[0].agenda

    assert days == {1: [[13, 17]], 2: [[8, 12]],
                    3: [[13, 17]], 4: [[8, 12]],
                    5: [[13, 17]], 6: [None], 7: [None]}


def test_grouping_rules_largetime(rules):
    res = group_rules(rules)
    days = filter(lambda x: x.code == 'OUT-SDX-10', res)[0].agenda

    assert days == {1: [[12, 24], [0, 8]], 2: [[0, 24]],
                    3: [[0, 24]], 4: [[0, 24]],
                    5: [[0, 24]], 6: [[0, 24]],
                    7: [[0, 24]]}


def test_grouping_rules_alltime(rules):
    res = group_rules(rules)
    days = filter(lambda x: x.code == 'SD-TT', res)[0].agenda

    assert days == {1: [[0, 24]], 2: [[0, 24]],
                    3: [[0, 24]], 4: [[0, 24]],
                    5: [[0, 24]], 6: [[0, 24]],
                    7: [[0, 24]]}


def test_on_restrictions():
    rule_view = [
        {
            "code": "SU-LD-A",
            "season_end": "12-01",
            "description": "P 09h30-10h30 LUN. 1 AVRIL AU 1 DEC.",
            "restrict_typ": None,
            "season_start": "04-01",
            "special_days": None,
            "time_max_parking": None,
            'agenda': {
                '1': [[9.5, 10.5]], '3': [None],
                '2': [None], '5': [None],
                '4': [None], '7': [None],
                '6': [None]}
        },
        {
            "code": "R-PF",
            "season_end": None,
            "description": "P RESERVE S3R 09h-23h",
            "restrict_typ": "permit",
            "season_start": None,
            "special_days": None,
            "time_max_parking": None,
            'agenda': {
                '1': [[9.0, 23.0]], '3': [[9.0, 23.0]],
                '2': [[9.0, 23.0]], '5': [[9.0, 23.0]],
                '4': [[9.0, 23.0]], '7': [[9.0, 23.0]],
                '6': [[9.0, 23.0]]}
        }
    ]
    assert on_restriction(rule_view, '2015-04-07T09:30', 1) == True

    # add test time_maxparking AND season start
    # add test duration > 1, 2 days


def test_season_matching():
    print season_matching(1, 1, 1, 4, 1, 4)
    assert season_matching(1, 1, 1, 4, 1, 4) == True
    assert season_matching(1, 1, 1, 4, 1, 1) == True
    assert season_matching(1, 1, 1, 4, 10, 4) == False
    assert season_matching(1, 12, 1, 4, 1, 4) == True
    assert season_matching(1, 12, 1, 4, 2, 4) == False
    assert season_matching(1, 4, 1, 12, 1, 2) == False
    assert season_matching(1, 3, 1, 1, 1, 2) == False