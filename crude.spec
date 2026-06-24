Name:           crude
Version:        1.4
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

The sites ship as their own binaries: crude-atdw (ATDW tourism listings),
crude-skal (Skal Australia member portal), crude-rezdy (Rezdy products,
availability, and bookings), crude-deputy (Deputy workforce management),
crude-sonas (Sonas wedding-venue software), crude-xero (Xero accounting),
crude-airwallex (Airwallex global payments and transactions), crude-clover
(Clover POS orders and catalog), and crude-facebook (Facebook Pages:
posts, insights, comments). The crude command
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
    crude-rezdy:crude_rezdy.cli \
    crude-deputy:crude_deputy.cli \
    crude-sonas:crude_sonas.cli \
    crude-xero:crude_xero.cli \
    crude-airwallex:crude_airwallex.cli \
    crude-clover:crude_clover.cli \
    crude-facebook:crude_facebook.cli; do
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
/usr/bin/crude-deputy
/usr/bin/crude-sonas
/usr/bin/crude-xero
/usr/bin/crude-airwallex
/usr/bin/crude-clover
/usr/bin/crude-facebook
/usr/lib/python*/site-packages/crude_*/
/usr/lib/python*/site-packages/crude-*.dist-info/

%changelog
* Tue Jun 24 2026 Weiwu Zhang <a@colourful.land> - 1.4-1
- Add crude-facebook: Facebook Pages client via the Meta Graph API. Covers
  posts (list, get, create, edit, hide/unhide, delete), page insights, and
  comments. Auth via a System User token stored in the durable state store.
  The binary replaces crude-meta; Instagram support is deferred.

* Thu Jun 19 2026 Weiwu Zhang <a@colourful.land> - 1.3.1-1
- crude-sonas: add template edit (T&C/policy write path), surface terms
  create/answer/delete verbs, fix event-id false-negative in per-event reads.
- crude-common: extract HttpSession shared transport; split cliutil into output
  and writeio modules; surface resource-group verbs in group-level help.

* Fri Jun 19 2026 Weiwu Zhang <a@colourful.land> - 1.3.0-1
- Add crude-clover: command-line client for the AP Clover POS REST API. Orders
  (line items, modifications, payments, refunds expanded), catalog dump, and
  flatten to the legacy Square item-level CSV layout so one analysis spans a
  Square-to-Clover switch. Full resource surface (inventory, customers,
  employees, merchant config, payments/refunds/credits) as list/get with
  confirm-gated create/update/delete on writable resources, a generic resource
  passthrough, and status/scopes commands.

* Wed Jun 18 2026 Weiwu Zhang <a@colourful.land> - 1.2.1-1
- Add crude-airwallex: command-line client for Airwallex. Covers Payouts
  (beneficiaries, transfers, FX conversions), Payments Acceptance (payment
  intents, consents, payment methods, authorisations), and Financial
  Transactions. Auth via API key stored in the durable state store.
- crude-xero: extend with Files, Assets, Projects, Payroll AU, BankFeeds,
  and Finance products; fix Payroll API base URL (payroll.xro/1.0).
- crude-sonas: render and filter event dates in local time, not UTC.

* Wed Jun 17 2026 Weiwu Zhang <a@colourful.land> - 1.2.0-1
- Add crude-xero: Xero accounting over the official OAuth2 APIs. Accounting API
  CRUD (accounts, bank transactions, contacts, invoices, bills, credit notes,
  items, payments, purchase orders, quotes, manual journals, tax rates, tracking
  categories, reports, attachments, history) with first-page-by-default listing
  plus --all/--limit, and multi-tenant selection (--tenant). The one-time
  crude-xero auth runs a browser consent (localhost loopback, or --manual);
  the rotating token is stored and renewed automatically.
- Durable token store: site tokens persist under $XDG_STATE_HOME/crude (default
  ~/.local/state/crude) via a shared crude_common.statestore, so they survive a
  reboot; crude-atdw, crude-skal and crude-sonas adopt it.

* Tue Jun 16 2026 Weiwu Zhang <a@colourful.land> - 1.1.2-1
- crude-rezdy: full CRUD over the Rezdy Supplier API (products, availability,
  bookings, customers, extras, pickup lists; category/rate/resource assignment;
  manifest check-in; voucher/company reads). Previously read-only.
- crude-atdw: add listing create (POST /api/listings); new listings start as a
  draft until submitted.
- crude-skal: add the benefit resource (the global Skål benefits register).
- install-claude-command advertises the new write verbs.

* Thu Jun 11 2026 Weiwu Zhang <a@colourful.land> - 1.1.1-1
- Add crude-sonas: command-line client for Sonas wedding-venue software.
  Core enquiry verbs cover event lifecycle, guests, timelines, notes,
  messages, documents, terms, service bookings, transactions, and invoices.
  T2 scheduling (availability, appointment, tasting); T3 catalog reads
  (supplier, service, drinks-package, package, template, category, venue,
  user, report); finance and mail verbs; live smoke-test suite.

* Sat May 30 2026 Weiwu Zhang <a@colourful.land> - 1.1.0-1
- Add crude-deputy: command-line client for Deputy workforce management.
  Curated sub-apps for employee, roster, area, timesheet, and leave; a generic
  resource sub-app reaches any Deputy object with QUERY operators, schema info,
  and full CRUD; /me shows the token owner.
- Multi-account support across all site CLIs: the bare [site] section is the
  default account, [site.<name>] subtables are named accounts, selected with
  --account/-a or $CRUDE_ACCOUNT.
- crude-rezdy: timezone is now a required config field; date filters convert
  the typed day to UTC before comparing against Rezdy's dateUpdated timestamps.

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
