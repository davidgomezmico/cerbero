# cerbero - a multi-platform build system for Open Source software
# Copyright (C) 2012 Andoni Morales Alastruey <ylatuya@gmail.com>
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Library General Public
# License as published by the Free Software Foundation; either
# version 2 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Library General Public License for more details.
#
# You should have received a copy of the GNU Library General Public
# License along with this library; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place - Suite 330,
# Boston, MA 02111-1307, USA.

import os
import shutil
import tempfile

from cerbero.config import Architecture, DEFAULT_PACKAGER
from cerbero.errors import FatalError, EmptyPackageError
from cerbero.packages import PackagerBase, PackageType
from cerbero.packages.disttarball import DistTarball
from cerbero.packages.linux import LinuxPackager
from cerbero.packages.package import MetaPackage
from cerbero.utils import shell, _
from cerbero.utils import messages as m


SPEC_TPL = '''
%%define _topdir %(topdir)s
%%define _package_name %(package_name)s

Name:           %(p_prefix)s%(name)s
Version:        %(version)s
Release:        1
Summary:        %(summary)s
Source:         %(source)s
Group:          Applications/Internet
License:        %(licenses)s
Prefix:         %(prefix)s
Packager:       %(packager)s
Vendor:         %(vendor)s
%(url)s
%(requires)s

%%description
%(description)s

%(devel_package)s

%%prep
%%setup -n %%{_package_name}

%%build

%%install
mkdir -p $RPM_BUILD_ROOT/%%{prefix}
cp -r $RPM_BUILD_DIR/%%{_package_name}/* $RPM_BUILD_ROOT/%%{prefix}

%%clean
rm -rf $RPM_BUILD_ROOT

%%files
%(files)s

%(devel_files)s
'''


DEVEL_PACKAGE_TPL = '''
%%package devel
%(requires)s
Summary: %(summary)s
Provides: %(p_prefix)s%(name)s-devel

%%description devel
%(description)s
'''

META_SPEC_TPL = '''
%%define _topdir %(topdir)s
%%define _package_name %(package_name)s

Name:           %(p_prefix)s%(name)s
Version:        %(version)s
Release:        1
Summary:        %(summary)s
Group:          Applications/Internet
License:        %(licenses)s
Packager:       %(packager)s
Vendor:         %(vendor)s
%(url)s

%(requires)s

%%description
%(description)s

%%prep

%%build

%%install

%%clean
rm -rf $RPM_BUILD_ROOT

%%files
'''

REQUIRE_TPL = 'Requires: %s\n'
DEVEL_TPL = '%%files devel \n%s'
URL_TPL = 'URL: %s\n'

class RPMPackager(LinuxPackager):

    def __init__(self, config, package, store):
        LinuxPackager.__init__(self, config, package, store)

    def create_tree(self, tmpdir):
        # create a tmp dir to use as topdir
        if tmpdir is None:
            tmpdir = tempfile.mkdtemp()
            for d in ['BUILD', 'SOURCES', 'RPMS', 'SRPMS', 'SPECS']:
                os.mkdir(os.path.join(tmpdir, d))
        return (tmpdir, os.path.join(tmpdir, 'RPMS'),
                os.path.join(tmpdir, 'SOURCES'))

    def setup_source(self, tarball, tmpdir, packagedir, srcdir):
        # move the tarball to SOURCES
        shutil.move(tarball, srcdir)
        tarname = os.path.split(tarball)[1]
        return tarname

    def prepare(self, tarname, tmpdir, packagedir, srcdir):
        requires = self._get_requires(PackageType.RUNTIME)
        runtime_files  = self._files_list(PackageType.RUNTIME)

        if self.devel:
            devel_package, devel_files = self._devel_package_and_files()
        else:
            devel_package, devel_files = ('', '')

        if isinstance(self.package, MetaPackage):
            template = META_SPEC_TPL
        else:
            template = SPEC_TPL

        self.package.has_devel_package = bool(devel_files)

        licenses = [self.package.license]
        if not isinstance(self.package, MetaPackage):
            licenses.extend(self.recipes_licenses())
            licenses = sorted(list(set(licenses)))

        self._spec_str = template % {
                'name': self.package.name,
                'p_prefix': self.package_prefix,
                'version': self.package.version,
                'package_name': self.full_package_name,
                'summary': self.package.shortdesc,
                'description': self.package.longdesc if self.package.longdesc != 'default' else self.package.shortdesc,
                'licenses': ' and '.join([l.acronym for l in licenses]),
                'packager': self.packager,
                'vendor': self.package.vendor,
                'url': URL_TPL % self.package.url if self.package.url != 'default' else '',
                'requires': requires,
                'prefix': self.install_dir,
                'source': tarname,
                'topdir': tmpdir,
                'devel_package': devel_package,
                'devel_files': devel_files,
                'files':  runtime_files}

        self.spec_path = os.path.join(tmpdir, '%s.spec' % self.package.name)
        with open(self.spec_path, 'w') as f:
            f.write(self._spec_str)

    def build(self, output_dir, tarname, tmpdir, packagedir, srcdir):
        if self.config.target_arch == Architecture.X86:
            target = 'i686-redhat-linux'
        elif self.config.target_arch == Architecture.X86_64:
            target = 'x86_64-redhat-linux'
        else:
            raise FatalError(_('Architecture %s not supported') % \
                             self.config.target_arch)
        shell.call('rpmbuild -bb --target %s %s' % (target, self.spec_path))

        paths = []
        for d in os.listdir(packagedir):
            for f in os.listdir(os.path.join(packagedir, d)):
                out_path = os.path.join(output_dir, f)
                if os.path.exists(out_path):
                    os.remove(out_path)
                paths.append(out_path)
                shutil.move(os.path.join(packagedir, d, f), output_dir)
        return paths

    def _get_requires(self, package_type):
        devel_suffix = ''
        if package_type == PackageType.DEVEL:
            devel_suffix = '-devel'
        deps = self.get_requires(package_type, devel_suffix)
        return reduce(lambda x, y: x + REQUIRE_TPL % y, deps, '')

    def _files_list(self, package_type):
        if isinstance(self.package, MetaPackage):
            return ''
        files = self.files_list(package_type)
        for f in [x for x in files if x.endswith('.py')]:
            if f+'c' not in files:
                files.append(f+'c')
            if f+'o' not in files:
                files.append(f+'o')
        return '\n'.join([os.path.join('%{prefix}',  x) for x in files])

    def _devel_package_and_files(self):
        args = {}
        args['summary'] = 'Development files for %s' % self.package.name
        args['description'] = args['summary']
        args['requires'] =  self._get_requires(PackageType.DEVEL)
        args['name'] = self.package.name
        args['p_prefix'] = self.package_prefix
        try:
            devel = DEVEL_TPL % self._files_list(PackageType.DEVEL)
        except EmptyPackageError:
            devel = ''
        return DEVEL_PACKAGE_TPL % args, devel


class Packager(object):

    def __new__(klass, config, package, store):
        return RPMPackager(config, package, store)


def register():
    from cerbero.packages.packager import register_packager
    from cerbero.config import Distro
    register_packager(Distro.REDHAT, Packager)
