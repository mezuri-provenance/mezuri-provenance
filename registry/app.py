#!/usr/bin/env python3

from flask import Flask, abort, make_response, jsonify
from flask_restful import Api, Resource, reqparse, fields, marshal
import json

from utilities import temporary_dir, working_dir, SPEC_FILENAME
from utilities.git import Git

registry = Flask(__name__, static_url_path='')
registry_api = Api(registry)


def fetch_remote_spec(remote_url: str, version_hash: str, version_tag: str):
    with temporary_dir() as directory:
        if not Git.clone(remote_url, directory):
            abort(make_response(jsonify({'error': 'Remote repository is not readable'}), 400))

        with working_dir(directory):
            if Git.rev_parse(version_tag) != version_hash:
                abort(make_response(jsonify({'error': 'Remote repository version does not match'}), 400))
            Git.checkout(version_hash)
            with open(SPEC_FILENAME) as f:
                return json.load(f)


@registry.errorhandler(409)
def conflict(_):
    return make_response(jsonify({'error': 'Component already exists'}), 409)


operator_list_fields = fields.List(fields.Nested({
    'name': fields.String,
    'uri': fields.Url(endpoint='operator', absolute=True),
}))
operator_fields = {
    'name': fields.String,
    'uri': fields.Url(endpoint='operator', absolute=True),
    'gitRemoteUrl': fields.String,
    'versions': fields.List(fields.String),
}
operators = {}


class OperatorListAPI(Resource):
    def __init__(self):
        self.parser = reqparse.RequestParser()
        self.parser.add_argument('name', type=str, required=True,
                                 help='Operator name not provided',
                                 location='json')
        self.parser.add_argument('gitRemoteUrl', type=str, required=True,
                                 help='Operator git remote url not provided',
                                 location='json')
        super().__init__()

    def get(self):
        return {'components': marshal(operators.values(), operator_list_fields)}

    def post(self):
        args = self.parser.parse_args()

        if args.name in operators:
            abort(409)

        operator = {
            'name': args.name,
            'gitRemoteUrl': args.gitRemoteUrl,
            'versions': []
        }
        operators[args.name] = operator

        return {'component': marshal(operator, operator_fields)}, 201


class OperatorAPI(Resource):
    def __init__(self):
        self.parser = reqparse.RequestParser()
        self.parser.add_argument('tag', type=str, required=True,
                                 help='Tag to be published not provided',
                                 location='json')

    def get(self, name: str):
        if name not in operators:
            abort(make_response(jsonify({'error': 'Operator does not exist'}), 404))

        return {'component': marshal(operators[name], operator_fields)}


registry_api.add_resource(OperatorListAPI, '/operators', endpoint='operators')
registry_api.add_resource(OperatorAPI, '/operators/<string:name>', endpoint='operator')


operator_version_for_list_fields = {
    'version': fields.String,
    'uri': fields.Url(endpoint='operator_version', absolute=True),
    'hash': fields.String
}
operator_version_fields = {
    'version': fields.String,
    'uri': fields.Url(endpoint='operator_version', absolute=True),
    'hash': fields.String,
    'operator_name': fields.String,
    'spec': fields.Raw
}
operator_versions = []


class OperatorVersionListAPI(Resource):
    def __init__(self):
        self.parser = reqparse.RequestParser()
        self.parser.add_argument('version', type=str, required=True,
                                 help='Version to be published not provided',
                                 location='json')
        self.parser.add_argument('version_tag', type=str, required=True,
                                 help='Version tag not provided',
                                 location='json')
        self.parser.add_argument('version_hash', type=str, required=True,
                                 help='Version hash not provided',
                                 location='json')

    def get(self, operator_name):
        if operator_name not in operators:
            abort(make_response(jsonify({'error': 'Operator does not exist'}), 404))

        return {'versions': [marshal(version, operator_version_for_list_fields)
                             for version in operator_versions
                             if version['operator_name'] == operator_name]}

    def post(self, operator_name):
        operator = operators.get(operator_name, None)
        if operator is None:
            abort(make_response(jsonify({'error': 'Operator does not exist'}), 404))

        args = self.parser.parse_args()
        if args.version in operator['versions']:
            abort(409)

        # TODO (dibyo): Fetch spec from git repository for specific version.
        spec = fetch_remote_spec(operator['gitRemoteUrl'], args.version_hash, args.version_tag)
        operator_version = {
            'version': args.version,
            'hash': args.version_hash,
            'operator_name': operator_name,
            'spec': spec
        }
        operator_versions.append(operator_version)
        operator['versions'].append(args.version)

        return {'version': marshal(operator_version, operator_version_fields)}, 201


class OperatorVersionAPI(Resource):
    def get(self, operator_name, version):
        if operator_name not in operators:
            abort(make_response(jsonify({'error': 'Operator does not exist'}), 404))

        for operator_version in operator_versions:
            if operator_version['operator_name'] == operator_name:
                if operator_version['version'] == version:
                    return {
                        'operator_version': marshal(operator_version, operator_version_fields)
                    }

        abort(make_response(jsonify({'error': 'Operator version does not exist'}), 404))


registry_api.add_resource(OperatorVersionListAPI, '/operators/<operator_name>/versions',
                          endpoint='operator_versions')
registry_api.add_resource(OperatorVersionAPI, '/operators/<operator_name>/versions/<version>',
                          endpoint='operator_version')


if __name__ == '__main__':
    registry.run(debug=True)
