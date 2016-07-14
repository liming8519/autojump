# -*- coding: utf-8 -*-
"""
  Copyright © 2008-2012 Joel Schaerer
  Copyright © 2012-2016 William Ting

  * This program is free software; you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation; either version 3, or (at your option)
  any later version.

  * This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  * You should have received a copy of the GNU General Public License
  along with this program; if not, write to the Free Software Foundation, Inc.,
  51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
"""
from __future__ import print_function

import os
import sys
from itertools import chain
from math import sqrt
from operator import attrgetter
from operator import itemgetter

from autojump import version
from autojump.data import dictify
from autojump.data import entriefy
from autojump.data import Entry
from autojump.data import load
from autojump.data import save
from lib.argparse import ArgumentParser
from autojump.match import match_anywhere
from autojump.match import match_consecutive
from autojump.match import match_fuzzy
from autojump.utils import first
from autojump.utils import get_pwd
from autojump.utils import get_tab_entry_info
from autojump.utils import has_uppercase
from autojump.utils import is_autojump_sourced
from autojump.utils import is_osx
from autojump.utils import is_windows
from autojump.utils import last
from autojump.utils import print_entry
from autojump.utils import print_local
from autojump.utils import print_tab_menu
from autojump.utils import sanitize
from autojump.utils import take
from autojump.utils import unico

if sys.version_info[0] == 3:
    ifilter = filter
    imap = map
    os.getcwdu = os.getcwd
else:
    from itertools import ifilter
    from itertools import imap


FUZZY_MATCH_THRESHOLD = 0.6
TAB_ENTRIES_COUNT = 9
TAB_SEPARATOR = '__'


def set_defaults():
    config = {}

    if is_osx():
        data_home = os.path.join(
            os.path.expanduser('~'),
            'Library',
            'autojump')
    elif is_windows():
        data_home = os.path.join(
            os.getenv('APPDATA'),
            'autojump')
    else:
        data_home = os.getenv(
            'XDG_DATA_HOME',
            os.path.join(
                os.path.expanduser('~'),
                '.local',
                'share',
                'autojump'))

    config['data_path'] = os.path.join(data_home, 'autojump.txt')
    config['backup_path'] = os.path.join(data_home, 'autojump.txt.bak')

    return config


def parse_arguments():
    parser = ArgumentParser(
        description='Automatically jump to directory passed as an argument.',
        epilog='Please see autojump(1) man pages for full documentation.')
    parser.add_argument(
        'directory', metavar='DIRECTORY', nargs='*', default='',
        help='directory to jump to')
    parser.add_argument(
        '-a', '--add', metavar='DIRECTORY',
        help='add path')
    parser.add_argument(
        '-i', '--increase', metavar='WEIGHT', nargs='?', type=int,
        const=10, default=False,
        help='increase current directory weight')
    parser.add_argument(
        '-d', '--decrease', metavar='WEIGHT', nargs='?', type=int,
        const=15, default=False,
        help='decrease current directory weight')
    parser.add_argument(
        '--complete', action='store_true', default=False,
        help='used for tab completion')
    parser.add_argument(
        '--purge', action='store_true', default=False,
        help='remove non-existent paths from database')
    parser.add_argument(
        '-s', '--stat', action='store_true', default=False,
        help='show database entries and their key weights')
    parser.add_argument(
        '-v', '--version', action='version', version='%(prog)s v' +
        str(version), help='show version information')

    return parser.parse_args()


def add_path(data, path, weight=10):
    """
    Add a new path or increment an existing one.

    os.path.realpath() is not used because it's preferable to use symlinks
    with resulting duplicate entries in the database than a single canonical
    path.
    """
    path = unico(path).rstrip(os.sep)
    if path == os.path.expanduser('~'):
        return data, Entry(path, 0)

    data[path] = sqrt((data.get(path, 0) ** 2) + (weight ** 2))

    return data, Entry(path, data[path])


def decrease_path(data, path, weight=15):
    """Decrease or zero out a path."""
    path = unico(path).rstrip(os.sep)
    data[path] = max(0, data.get(path, 0) - weight)
    return data, Entry(path, data[path])


def detect_smartcase(needles):
    """
    If any needles contain an uppercase letter then use case sensitive
    searching. Otherwise use case insensitive searching.
    """
    return not any(imap(has_uppercase, needles))


def find_matches(entries, needles, check_entries=True):
    """Return an iterator to matching entries."""
    # TODO(wting|2014-02-24): replace assertion with unit test
    assert isinstance(needles, list), 'Needles must be a list.'
    ignore_case = detect_smartcase(needles)

    try:
        pwd = os.getcwdu()
    except OSError:
        pwd = None

    # using closure to prevent constantly hitting hdd
    def is_cwd(entry):
        return os.path.realpath(entry.path) == pwd

    if check_entries:
        path_exists = lambda entry: os.path.exists(entry.path)
    else:
        path_exists = lambda _: True

    data = sorted(
        entries,
        key=attrgetter('weight', 'path'),
        reverse=True)

    return ifilter(
        lambda entry: not is_cwd(entry) and path_exists(entry),
        chain(
            match_consecutive(needles, data, ignore_case),
            match_fuzzy(needles, data, ignore_case),
            match_anywhere(needles, data, ignore_case)))


def handle_tab_completion(needle, entries):
    tab_needle, tab_index, tab_path = get_tab_entry_info(needle, TAB_SEPARATOR)

    if tab_path:
        print_local(tab_path)
    elif tab_index:
        get_ith_path = lambda i, iterable: last(take(i, iterable)).path
        print_local(get_ith_path(
            tab_index,
            find_matches(entries, [tab_needle], check_entries=False)))
    elif tab_needle:
        # found partial tab completion entry
        print_tab_menu(
            tab_needle,
            take(TAB_ENTRIES_COUNT, find_matches(
                entries,
                [tab_needle],
                check_entries=False)),
            TAB_SEPARATOR)
    else:
        print_tab_menu(
            needle,
            take(TAB_ENTRIES_COUNT, find_matches(
                entries,
                [needle],
                check_entries=False)),
            TAB_SEPARATOR)


def purge_missing_paths(entries):
    """Remove non-existent paths from a list of entries."""
    exists = lambda entry: os.path.exists(entry.path)
    return ifilter(exists, entries)


def print_stats(data, data_path):
    for path, weight in sorted(data.items(), key=itemgetter(1)):
        print_entry(Entry(path, weight))

    print('________________________________________\n')
    print('%d:\t total weight' % sum(data.values()))
    print('%d:\t number of entries' % len(data))

    try:
        print_local(
            '%.2f:\t current directory weight' % data.get(os.getcwdu(), 0))
    except OSError:
        # current directory no longer exists
        pass

    print('\ndata:\t %s' % data_path)


def main(args):  # noqa
    # if not is_autojump_sourced() and not is_windows():
        # print("Please source the correct autojump file in your shell's")
        # print('startup file. For more information, please reinstall autojump')
        # print('and read the post installation instructions.')
        # return 1

    config = set_defaults()

    # all arguments are mutually exclusive
    if args.add:
        save(config, first(add_path(load(config), args.add)))
    elif args.complete:
        handle_tab_completion(
            needle=first(chain(sanitize(args.directory), [''])),
            entries=entriefy(load(config)))
    elif args.decrease:
        data, entry = decrease_path(load(config), get_pwd(), args.decrease)
        save(config, data)
        print_entry(entry)
    elif args.increase:
        data, entry = add_path(load(config), get_pwd(), args.increase)
        save(config, data)
        print_entry(entry)
    elif args.purge:
        old_data = load(config)
        new_data = dictify(purge_missing_paths(entriefy(old_data)))
        save(config, new_data)
        print('Purged %d entries.' % (len(old_data) - len(new_data)))
    elif args.stat:
        print_stats(load(config), config['data_path'])
    elif not args.directory:
        # Return best match.
        entries = entriefy(load(config))
        print_local(first(chain(
            imap(attrgetter('path'), find_matches(entries, [''])),
            # always return a path to calling shell functions
            ['.'])))
    else:
        entries = entriefy(load(config))
        needles = sanitize(args.directory)
        tab_needle, tab_index, tab_path = \
            get_tab_entry_info(first(needles), TAB_SEPARATOR)

        # Handle `j foo__`, assuming first index.
        if not tab_path and not tab_index \
                and tab_needle and needles[0] == tab_needle + TAB_SEPARATOR:
            tab_index = 1

        if tab_path:
            print_local(tab_path)
        elif tab_index:
            get_ith_path = lambda i, iterable: last(take(i, iterable)).path
            print_local(
                get_ith_path(
                    tab_index,
                    find_matches(entries, [tab_needle])))
        else:
            print_local(first(chain(
                imap(attrgetter('path'), find_matches(entries, needles)),
                # always return a path to calling shell functions
                ['.'])))

    return 0


if __name__ == '__main__':
    sys.exit(main(parse_arguments()))
