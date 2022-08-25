#!/usr/bin/python3

"""
Copyright (c) 2022, Ian Santopietro
All rights reserved.

This file is part of RepoLib.

RepoLib is free software: you can redistribute it and/or modify
it under the terms of the GNU Lesser General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

RepoLib is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public License
along with RepoLib.  If not, see <https://www.gnu.org/licenses/>.
"""

import logging
from sys import prefix

from repolib.key import SourceKey

from .source import Source, SourceError
from .file import SourceFile
from . import util

try:
    from launchpadlib.launchpad import Launchpad
    from lazr.restfulclient.errors import BadRequest, NotFound, Unauthorized
except ImportError:
    Launchpad = None

PPA_BASE = 'http://ppa.launchpad.net/'
PPA_DIST = 'ubuntu'
PPA_FORMAT = util.SourceFormat.LEGACY
PPA_COMPS = 'main'

prefix = 'ppa'
delineator = ':'

class PPASource(Source):
    """ PPA Source shortcut

    These are given in the format ppa:owner/name. Much of this code is adapted 
    from SoftwareProperties.

    Arguments:
        shortcut (str): The ppa: shortcut to process
        fetch_data (bool): Whether to try and fetch metadata from Launchpad.
    """

    @staticmethod
    def validator(shortcut:str) -> None:
        """Determine whether a PPA shortcut is valid.
        
        Arguments:
            shortcut(str): The shortcut to validate
        
        Returns: bool
            `True` if the PPA is valid, otherwise False
        """

        if shortcut.startswith(prefix):
            shortlist = shortcut.split('/')
            if len(shortlist) > 1:
                return True
        return False

    def __init__(self, *args, line=None, fetch_data=True, **kwargs):
        if line:
            if not line.startswith('ppa:'):
                raise SourceError(f'The PPA shortcut {line} is malformed')
        super().__init__(args, kwargs)
        self.log = logging.getLogger(__name__)
        self.line = line
        self.ppa = None
        self.twin_source = True
        if line:
            self.load_from_shortcut(self.line)

    def load_from_shortcut(self, shortcut:str='', meta:bool=True, key:bool=True) -> None:
        """Translates the shortcut line into a full repo.
        
        Arguments:
            shortcut(str): The shortcut to load, if one hasn't been loaded yet.
            meta(bool): Whether to fetch repo metadata from Launchpad
            key(bool): Whether to fetch and install a signing key
        """
        self.reset_values()
        if shortcut:
            self.line = shortcut
        
        if not self.line:
            raise SourceError('No PPA shortcut provided')
        
        if not self.validator(self.line):
            raise SourceError(f'The line {self.line} is malformed')
        
        line = self.line.replace(prefix + delineator, '')
        self.info_parts = line.split('/')
        ppa_owner = self.info_parts[0]
        ppa_name = self.info_parts[1]

        self.ident = f'{prefix}-{ppa_owner}-{ppa_name}'
        if f'{self.ident}.{PPA_FORMAT.value}' not in util.files:
            new_file = SourceFile(name=self.ident)
            new_file.format = PPA_FORMAT
            self.file = new_file
            util.files[str(self.file.path)] = self.file
        else:
            self.file = util.files[str(self.file.path)]
        
        self.file.add_source(self)
        
        self.name = self.ident
        self.uris = [f'{PPA_BASE}/{ppa_owner}/{ppa_name}/{PPA_DIST}']
        self.suites = [util.DISTRO_CODENAME]
        self.components = [PPA_COMPS]

        if meta or key:
            self.ppa = get_info_from_lp(ppa_owner, ppa_name)
        
        if self.ppa and meta:
            self.name = self.ppa.displayname
        
        if self.ppa and key:
            key = SourceKey(name=self.ident)
            if str(key.path) not in util.keys:
                key.load_key_data(fingerprint=self.ppa.fingerprint)
                util.keys[str(key.path)] = key
                self.key = key
            else:
                self.key = util.keys[key.path]
            self.signed_by = str(self.key.path)

        self.enabled = True


class PPA:
    """ An object to fetch data from PPAs. 
    
    Portions of this class were adapted from Software Properties
    """

    def __init__(self, teamname, ppaname):
        self.teamname = teamname
        self.ppaname = ppaname
        self._lap = None
        self._lpteam = None
        self._lpppa = None
        self._signing_key_data = None
        self._fingerprint = None

    @property
    def lap(self):
        """ The Launchpad Object."""
        if not self._lap:
            self._lap = Launchpad.login_anonymously(
                f'{self.__module__}.{self.__class__.__name__}',
                service_root='production',
                version='devel'
            )
        return self._lap

    @property
    def lpteam(self):
        """ The Launchpad object for the PPA's owner."""
        if not self._lpteam:
            try:
                self._lpteam = self.lap.people(self.teamname)
            except NotFound as err:
                msg = f'User/Team "{self.teamname}" not found'
                raise SourceError(msg) from err
            except Unauthorized as err:
                msg = f'Invalid user/team name "{self.teamname}"'
                raise SourceError(msg) from err
        return self._lpteam

    @property
    def lpppa(self):
        """ The Launchpad object for the PPA."""
        if not self._lpppa:
            try:
                self._lpppa = self.lpteam.getPPAByName(name=self.ppaname)
            except NotFound as err:
                msg = f'PPA "{self.teamname}/{self.ppaname}"" not found'
                raise SourceError(msg) from err
            except BadRequest as err:
                msg = f'Invalid PPA name "{self.ppaname}"'
                raise SourceError(msg) from err
        return self._lpppa

    @property
    def description(self):
        """str: The description of the PPA."""
        return self.lpppa.description

    @property
    def displayname(self):
        """ str: the fancy name of the PPA."""
        return self.lpppa.displayname

    @property
    def fingerprint(self):
        """ str: the fingerprint of the signing key."""
        if not self._fingerprint:
            self._fingerprint = self.lpppa.signing_key_fingerprint
        return self._fingerprint


def get_info_from_lp(owner_name, ppa):
    """ Attempt to get information on a PPA from launchpad over the internet.

    Arguments:
        owner_name (str): The Launchpad user owning the PPA.
        ppa (str): The name of the PPA

    Returns:
        json: The PPA information as a JSON object.
    """
    ppa = PPA(owner_name, ppa)
    return ppa
