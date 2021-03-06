from __future__ import unicode_literals

from prkng.api.public import api
from prkng.models import Checkins, City, Images, Reports, Slots, User, UserAuth
from prkng.login import facebook_signin, google_signin, email_register, email_signin, email_update
from prkng.utils import timestamp

import copy
from geojson import loads, FeatureCollection, Feature
from flask import Response, g, request
from flask.ext.restplus import Resource
import time


GEOM_TYPES = ('Point', 'LineString', 'Polygon',
              'MultiPoint', 'MultiLineString', 'MultiPolygon')

slot_props = (
    'id',
    'geojson',
    'rules',
    'button_location',
    'way_name'
)

@api.route('/areas', doc=False)
class ServiceAreaResource(Resource):
    def get(self):
        """
        Returns coverage area package versions and metadata
        """
        res = City.get_assets()

        return {
            "latest_version": max([x["version"] for x in res]),
            "versions": {
                x["version"]: x for x in res
            }
        }, 200


@api.route('/slot/<string:id>', doc=False)
class SlotResource(Resource):
    def get(self, id):
        """
        Returns the parking slot corresponding to the id
        """
        res = Slots.get_byid(id, slot_props)
        if not res:
            api.abort(404, "feature not found")

        res = res[0]
        return Feature(
            id=res[0],
            geometry=res[1],
            properties={
                field: res[num]
                for num, field in enumerate(slot_props[2:], start=2)
            }
        ), 200


slot_parser = api.parser()
slot_parser.add_argument(
    'radius',
    type=int,
    location='args',
    default=300,
    help='Radius search in meters; default is 300'
)
slot_parser.add_argument(
    'latitude',
    type=float,
    location='args',
    required=True,
    help='Latitude in degrees (WGS84)'
)
slot_parser.add_argument(
    'longitude',
    type=float,
    location='args',
    required=True,
    help='Longitude in degrees (WGS84)'
)
slot_parser.add_argument(
    'checkin',
    type=timestamp,
    location='args',
    default=time.strftime("%Y-%m-%dT%H:%M:%S"),
    help="Check-in timestamp in ISO 8601 ('2013-01-01T12:00'); default is now"
)
slot_parser.add_argument(
    'duration',
    type=float,
    location='args',
    default=0.5,
    help='Desired Parking time in hours; default is 30 min'
)
slot_parser.add_argument(
    'permit',
    type=str,
    location='args',
    default=False,
    help='Permit number to check availability for; can also use "all"'
)


@api.route('/slots', doc=False)
class SlotsResource(Resource):
    def get(self):
        """
        Returns slots around the point defined by (x, y)
        """
        args = slot_parser.parse_args()

        city = City.get(args['longitude'], args['latitude'])
        if not city:
            api.abort(404, "no feature found")

        res = Slots.get_within(
            city,
            args['longitude'],
            args['latitude'],
            args['radius'],
            args['duration'],
            slot_props,
            args['checkin'],
            args['permit'] in ['false', False],
            args['permit'] == 'all'
        )

        return FeatureCollection([
            Feature(
                id=feat['id'],
                geometry=feat['geojson'],
                properties={
                    "button_location": feat["button_location"],
                    "rules": feat["rules"],
                    "way_name": feat["way_name"]
                }
            )
            for feat in res
        ]), 200


token_parser = api.parser()
token_parser.add_argument(
    'access_token',
    type=str,
    location='form',
    help='Oauth2 user access token'
)


@api.route('/login/facebook', doc=False)
class LoginFacebook(Resource):
    def post(self):
        """
        Login with a facebook account.

        Existing user will automatically have a new API key generated
        """
        args = token_parser.parse_args()

        return facebook_signin(args['access_token'])


@api.route('/login/google', doc=False)
class LoginGoogle(Resource):
    def post(self):
        """
        Login with a google account.fields

        Existing user will automatically have a new API key generated
        """
        args = token_parser.parse_args()

        return google_signin(args['access_token'])


register_parser = api.parser()
register_parser.add_argument('email', required=True, type=str, location='form', help='user email')
register_parser.add_argument('password', required=True, type=str, location='form', help='user password')
register_parser.add_argument('name', required=True, type=unicode, location='form', help='user name')
register_parser.add_argument('gender', type=str, location='form', help='gender')
register_parser.add_argument('birthyear', type=str, location='form', help='birth year')
register_parser.add_argument('image_url', type=str, location='form', help='avatar URL')


@api.route('/register', doc=False)
class Register(Resource):
    def post(self):
        """
        Register a new account.
        """
        args = register_parser.parse_args()
        return email_register(**args)


email_parser = api.parser()
email_parser.add_argument('email', type=str, location='form', help='user email')
email_parser.add_argument('password', type=str, location='form', help='user password')


@api.route('/login/email', doc=False)
class LoginEmail(Resource):
    def post(self):
        """
        Login with en email account.
        """
        args = email_parser.parse_args()
        return email_signin(**args)


passwd_reset_parser = api.parser()
passwd_reset_parser.add_argument(
    'email', type=str, required=True, help='Email of account', location='form')


@api.route('/login/email/reset', doc=False)
class LoginEmailReset(Resource):
    def post(self):
        """
        Send an account password reset code
        """
        args = passwd_reset_parser.parse_args()
        user = User.get_byemail(args["email"].lower())
        if not user:
            return "Account not found", 400
        return UserAuth.send_reset_code("email${}".format(user.id), user.email)


passwd_change_parser = api.parser()
passwd_change_parser.add_argument(
    'email', type=str, required=True, help='Email of account to reset', location='form')
passwd_change_parser.add_argument(
    'code', type=str, required=True, help='Account reset code', location='form')
passwd_change_parser.add_argument(
    'passwd', type=str, required=True, help='New password', location='form')


@api.route('/login/email/changepass', doc=False)
class LoginEmailChangePass(Resource):
    def post(self):
        """
        Change an account's password via reset code
        """
        args = passwd_change_parser.parse_args()
        user = User.get_byemail(args["email"])
        if not user:
            return "Account not found", 404
        if not UserAuth.update_password("email${}".format(user.id), args["passwd"], args["code"]):
            return "Reset code incorrect", 400


# define header parser for the API key
api_key_parser = api.parser()
api_key_parser.add_argument(
    'X-API-KEY',
    type=str,
    location='headers',
    help='Prkng API Key'
)

# define the slot id parser
post_checkin_parser = copy.deepcopy(api_key_parser)
post_checkin_parser.add_argument(
    'slot_id', type=int, required=True, help='Slot identifier', location='form')

get_checkin_parser = copy.deepcopy(api_key_parser)
get_checkin_parser.add_argument(
    'limit', type=int, default=10, help='Slot identifier', location='query')

delete_checkin_parser = copy.deepcopy(api_key_parser)
delete_checkin_parser.add_argument(
    'checkin_id', type=int, required=True, help='Check-in identifier',
    location='form')


@api.route('/slot/checkin', doc=False)
class Checkin(Resource):
    @api.secure
    def get(self):
        """
        Get the list of last checkins.

        List has a max length of 10 checkins.
        """
        args = get_checkin_parser.parse_args()
        limit = min(args['limit'], 10)
        res = Checkins.get_all(g.user.id, limit)
        return res, 200

    @api.secure
    def post(self):
        """
        Add a new checkin
        """
        args = post_checkin_parser.parse_args()
        ok = Checkins.add(g.user.id, args['slot_id'])
        if not ok:
            api.abort(404, "No slot existing with this id")
        res = Checkins.get(g.user.id)
        return res, 201

    @api.secure
    def delete(self):
        """
        Deactivate an existing checkin
        """
        args = delete_checkin_parser.parse_args()
        Checkins.remove(g.user.id, args['checkin_id'])
        return "Resource deleted", 204


update_profile_parser = copy.deepcopy(api_key_parser)
update_profile_parser.add_argument('email', type=str, location='form', help='user email')
update_profile_parser.add_argument('password', type=str, location='form', help='user password')
update_profile_parser.add_argument('name', type=unicode, location='form', help='user name')
update_profile_parser.add_argument('gender', type=str, location='form', help='gender')
update_profile_parser.add_argument('birthyear', type=str, location='form', help='birth year')
update_profile_parser.add_argument('image_url', type=str, location='form', help='avatar URL')


@api.route('/user/profile', doc=False)
class Profile(Resource):
    @api.secure
    def get(self):
        """Get informations about a user"""
        return g.user.json, 200

    @api.secure
    def put(self):
        """Update user profile information"""
        args = update_profile_parser.parse_args()
        del args['X-API-KEY']
        return email_update(g.user, **args)


image_parser = copy.deepcopy(api_key_parser)
image_parser.add_argument(
    'image_type', type=str, required=True, help='Either "avatar" or "report"',
    location='form')
image_parser.add_argument(
    'file_name', type=str, required=True, help='File name of the image to be uploaded',
    location='form')

@api.route('/image', doc=False)
class Image(Resource):
    @api.secure
    def post(self):
        """
        Generate an S3 URL for image submission
        """
        args = image_parser.parse_args()
        data = Images.generate_s3_url(args["image_type"], args["file_name"])
        return data, 200


report_parser = copy.deepcopy(api_key_parser)
report_parser.add_argument(
    'slot_id', type=int, help='Slot identifier', location='form')
report_parser.add_argument(
    'latitude',
    type=float,
    location='form',
    required=True,
    help='Latitude in degrees (WGS84)'
)
report_parser.add_argument(
    'longitude',
    type=float,
    location='form',
    required=True,
    help='Longitude in degrees (WGS84)'
)
report_parser.add_argument('image_url', type=str, required=True,
    location='form', help='report image URL')
report_parser.add_argument('notes', type=str,
    location='form', help='report notes')


@api.route('/report', doc=False)
class Report(Resource):
    @api.secure
    def post(self):
        """Submit a report about incorrect data"""
        args = report_parser.parse_args()
        city = City.get(args['longitude'], args['latitude'])
        if not city:
            return "Resource created", 201

        Reports.add(g.user.id, city, args.get("slot_id", None), args["longitude"],
            args["latitude"], args.get("image_url", ""), args.get("notes", ""))
        return "Resource created", 201
