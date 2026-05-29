Name:           crude
Version:        1.0.2
Release:        1%{?dist}
Summary:        CRUD-style command-line clients for sites without a public API
License:        MIT
URL:            https://github.com/SmartLayer/crude
Source0:        %{url}/archive/refs/tags/v%{version}.tar.gz#/%{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
BuildRequires:  python3-pip
BuildRequires:  python3-wheel

Requires:       python3 >= 3.9
Requires:       python3-typer >= 0.9
Requires:       python3-requests >= 2.31
Requires:       python3-tomli-w >= 1.0

%description
crude provides command-line clients for reading and editing your own
records on sites that lack a usable public API, under one predictable
<site> <resource> <verb> grammar.

Three sites ship in this release, each as its own binary: crude-atdw
(ATDW tourism listings), crude-skal (Skal Australia member portal), and
crude-rezdy (Rezdy products, availability, and bookings). The crude command
lists them and carries the shared --version and install-claude-command flags.

%prep
%autosetup -n %{name}-%{version}

%build
python3 -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .

%install
# Unpack the wheel directly to work around Debian sysconfig patches when
# building on a Debian/Ubuntu host. On Fedora, replace this block with:
# %%pyproject_install
PYTHON_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
SITE_DIR=%{buildroot}/usr/lib/python${PYTHON_VER}/site-packages
mkdir -p "${SITE_DIR}" %{buildroot}/usr/bin
python3 -c "
import zipfile, sys
with zipfile.ZipFile(sys.argv[1]) as whl:
    whl.extractall(sys.argv[2])
" dist/crude-*.whl "${SITE_DIR}"

# Create one entry-point script per console_scripts entry in pyproject.toml.
for spec in \
    crude:crude_common.launcher \
    crude-atdw:crude_atdw.cli \
    crude-skal:crude_skal.cli \
    crude-rezdy:crude_rezdy.cli; do
    name=${spec%%:*}
    module=${spec##*:}
    cat > %{buildroot}/usr/bin/${name} << ENTRY
#!/usr/bin/python3
from ${module} import app
app()
ENTRY
    chmod 755 %{buildroot}/usr/bin/${name}
done

%files
%license LICENSE
%doc README.md
/usr/bin/crude
/usr/bin/crude-atdw
/usr/bin/crude-skal
/usr/bin/crude-rezdy
/usr/lib/python*/site-packages/crude_*/
/usr/lib/python*/site-packages/crude-*.dist-info/

%changelog
* Sat May 30 2026 Weiwu Zhang <a@colourful.land> - 1.0.2-1
- crude-rezdy booking: new cancellations subcommand, filtering by
  cancellation date (dateUpdated), with columns for product, session,
  paid/total, refund count, and internal notes.
- crude-rezdy booking list: new --updated-from / --updated-to and --all
  (auto-pagination); default table shows product, session date, paid/total,
  and last-updated date.
- crude-rezdy: paginate() added to the API client for auto-pagination.
- Claude command updated to document the new subcommand and flags.

* Fri May 29 2026 Weiwu Zhang <a@colourful.land> - 1.0.1-1
- Internal refactor: shared config discovery, config reading, and Claude Code
  command registration moved into crude_common.
- Add a live smoke-test suite (pytest, opt-in "live" marker).
- Claude Code command installs and keeps ~/.claude/commands/crude.md current
  on every run; the version stamp and staleness check are dropped.

* Mon May 25 2026 Weiwu Zhang <a@colourful.land> - 1.0.0-1
- Initial package. Unified command-line clients for ATDW, Skal, and Rezdy
  under a crude-<site> <resource> <verb> grammar.
