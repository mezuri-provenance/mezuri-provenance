#!/usr/bin/env python3

from collections import OrderedDict
from contextlib import contextmanager
import json
import os
from typing import Dict

from common import SPEC_FILENAME, SPEC_KEY, SPEC_PATH_KEY, SPEC_IOP_DECLARATION_KEY
from common.constructs import Version, DEFAULT_VERSION, VersionTag
from common.git import Git
from common.registry import RegistryClient, RegistryError

"""
Utilities for CLI.

specification.json format:
{
    "name": <component name>,
    "description": <component description>,
    "version": "0.0.0",
    "componentType": <component-type>,
    "publish": {
        "remote": {
            "name": <remote name>,
            "url": <remote url>
        }
        "registry": <registry url>
    }
}
"""

DEFAULT_REGISTRY = 'http://registry.mezuri.org'

TAG_NAME_FORMAT = 'mezuri/{component_type}/{version}'


def component_spec_defaults(component_type):
    return OrderedDict((
        ('name', None),
        ('componentType', component_type),
        ('description', None),
        ('version', DEFAULT_VERSION),
    ))


def input_name() -> str:
    name = input('Name (only a-z,-): ').strip()
    while len(name.split()) > 1:
        print('{} is not a valid Component name. Try again.'.format(name))
        name = input('Name (only a-z,-): ').strip()
    return name


def input_git_remote() -> Dict:
    remote_url = input('Git remote url: ').strip()
    remote_name = input('Git remote name: ').strip()
    return {
        'name': remote_name,
        'url': remote_url
    }


def input_registry() -> str:
    registry = input('registry [{}]: '.format(DEFAULT_REGISTRY)).strip()
    return registry if registry else DEFAULT_REGISTRY


def get_project_root_by_specification() -> str or None:
    directory = os.getcwd()
    while True:
        if os.path.exists(os.path.join(directory, SPEC_FILENAME)):
            return directory

        if directory == '/':
            return None

        directory = os.path.abspath(os.path.join(directory, os.path.pardir))


def specification_filename():
    project_root = get_project_root_by_specification()
    if project_root is None:
        return None

    return os.path.join(project_root, SPEC_FILENAME)


def specification():
    """Returns specifications and path to specifications."""
    filename = specification_filename()
    if filename is None:
        return None, None

    with open(filename) as f:
        spec = json.load(f, object_pairs_hook=OrderedDict)

    spec['version'] = Version(spec['version'])
    return spec, filename


def calculate_component_context(component_type, spec_defaults=None):
    spec = component_spec_defaults(component_type)
    if spec_defaults is not None:
        spec.update(spec_defaults)
    context = {SPEC_KEY: spec}

    saved_spec, path = specification()
    if saved_spec is not None:
        context[SPEC_KEY].update(saved_spec)
        context[SPEC_PATH_KEY] = path

    return context


def save_component_context(context):
    if SPEC_PATH_KEY in context:
        spec_to_save = OrderedDict()
        for k, v in context[SPEC_KEY].items():
            if type(v) == Version:
                v = str(v)
            spec_to_save[k] = v

        dump = json.dumps(spec_to_save, indent=4)
        with open(context[SPEC_PATH_KEY], 'w') as f:
            f.write(dump)


@contextmanager
def component_context(component_type: str, spec_defaults=None):
    ctx = calculate_component_context(component_type, spec_defaults)
    try:
        ctx_copy = OrderedDict(ctx)
        yield ctx_copy
        ctx = ctx_copy
    finally:
        save_component_context(ctx)


def component_init(component_type: str, spec_defaults=None):
    with component_context(component_type, spec_defaults) as ctx:
        if SPEC_PATH_KEY in ctx:
            # TODO(dibyo): Support initializing/re-initializing from passed in
            # JSON-file
            print('Component already initialized.')
            return 1

        spec = ctx[SPEC_KEY]
        spec['name'] = input_name()
        spec['description'] = input('Description: ').strip()
        version = input('Version [{}]: '.format(DEFAULT_VERSION)).strip()
        spec['version'] = Version(version) if version else DEFAULT_VERSION

        Git.init()
        ctx[SPEC_PATH_KEY] = os.path.join(os.getcwd(), SPEC_FILENAME)
    Git.add(SPEC_FILENAME)
    return 0


def component_commit(component_type: str, message: str, version: Version=None, spec_defaults=None):
    with component_context(component_type, spec_defaults) as ctx:
        if SPEC_PATH_KEY not in ctx:
            print('Component not initialized.')
            return 1

        spec = ctx[SPEC_KEY]

        # Check if IOP declaration has been added.
        if SPEC_IOP_DECLARATION_KEY not in spec:
            print('Component IOP declaration not added.')
            return 1

        # Check if version has been incremented.
        current_version = version if version is not None else spec['version']
        version_tag = VersionTag(component_type, spec['name'], current_version)
        prev_tags_raw = Git.tag.list()
        if prev_tags_raw:
            last_tag = max(VersionTag.parse(tag) for tag in prev_tags_raw)
            if version_tag <= last_tag:
                print('Version {} not greater than {}'.format(current_version, last_tag.version))
                return 1

        spec['version'] = current_version

    Git.add(spec['definition']['file'])
    Git.commit(message)
    Git.tag.create(str(version_tag), message)
    return 0


def component_publish(component_type: str, spec_defaults=None):
    tags = Git.tag.list()
    if not tags:
        print('Component does not have any versions to publish.')
        return 1
    tag_to_publish = max(VersionTag.parse(tag) for tag in tags)

    with component_context(component_type, spec_defaults) as ctx:
        if SPEC_PATH_KEY not in ctx:
            print('Component in not initialized.')
            return 1

        spec = ctx[SPEC_KEY]
        publish = spec.get('publish', None)

        if publish is None:  # Component has never been published before.
            remote_names = Git.remote.list()
            if not remote_names:
                remote = input_git_remote()
                Git.remote.add(remote['name'], remote['url'])
            else:
                remote_name = input('Git remote [{}]: '.format(', '.join(remote_names))).strip()
                if remote_name:
                    remote = {
                        'name': remote_name,
                        'url': Git.remote.url(remote_name)
                    }
                else:
                    remote = input_git_remote()
                    Git.remote.add(remote['name'], remote['url'])
            registry = input_registry()

            publish = {
                'remote': remote,
                'registry': registry
            }

        if not Git.push(publish['remote']['name']):
            print('Component could not be pushed to remote, possibly due to a conflict.')
            return 1
        spec['publish'] = publish

    if Git.commit('Update specification', substitute_author=True) is not None:
        tag_message = Git.tag.message(str(tag_to_publish))
        tag_to_publish = tag_to_publish.with_incremented_update_num()
        Git.tag.create(str(tag_to_publish), tag_message)

    if not Git.push(publish['remote']['name'], str(tag_to_publish)):
        print('Component version could not be pushed to remote, possibly due to a conflict.')
        return 1

    try:
        RegistryClient(publish['registry'], component_type, spec['name']).push(
            publish['remote']['url'],
            tag_to_publish,
            Git.tag.hash(str(tag_to_publish))
        )
    except RegistryError as e:
        print('Component could not be published: {}.'.format(e))
