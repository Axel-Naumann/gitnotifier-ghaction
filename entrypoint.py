"""
GitHub action to send one email for each commit since the last time it sent
email. Each email contains a beautiful diff.
"""

from os import environ
from string import Template
import json
import urllib.request
import html
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from github3 import login, GitHubError
from unidiff import PatchSet, PatchedFile


def get_github():
    """
    Create the GitHub object.
    """
    user = environ['GITHUB_ACTOR']
    # get one at https://github.com/settings/tokens/new - needs gist auth
    token = environ['INPUT_GITHUBTOKEN']
    return login(user, token)


def bump_rev_in_gist_and_get_old_rev(ghsession, repo, ref, newrev):
    """
    Updates the revision stored in the Gist for this ref.
    """
    gistname = "{}-lastrev.txt".format(ref)
    gistdescr = "GitNotifier action info for {}/{}"\
        .format(repo, environ['GITHUB_WORKFLOW'])
    print("::debug file={}:: gistname='{}' gistdescr='{}'"
          .format(__file__, gistname, gistdescr))

    gists = [g for g in ghsession.gists() if not g.public and g.description == gistdescr]
    print("::debug file={}:: gists='{}'".format(__file__, str(gists)))
    if len(gists) > 1:
        print("error:: file={}:: too many private gists with description '{}'!\n"
              "Found at {}\nPlease delete all but one!"
              .format(__file__, gistdescr, '\n  '.join([g.html_url for g in gists])))
        exit(1)

    oldrev = newrev + '~1'
    if not gists:
        files = {
            gistname : {
                'content': newrev
            }
        }
        gist = ghsession.create_gist(gistdescr, files, public=False)
    else:
        gist = gists[0]
        print("::debug file={}:: updating gist='{}'".format(__file__, str(type(gist))))
        files = gist.files
        if gistname in files:
            oldrev = files[gistname].content
        files[gistname] = {'content': newrev}
        gist.edit(gistdescr, files)
    return oldrev


def collect_revs(repo, oldrev, newrev):
    """
    Return the list of revs after oldrev up to (and including) newrev.
    """
    if oldrev != newrev + '~1':
        return [newrev]
    revs = []
    rev = newrev
    commit = repo.commit(rev)
    while True:
        revs.append(rev)
        commit = commit.parents
        rev = commit.sha
        if len(revs) > 100:
            print("warning:: file={}:: more than {} commits between '{}' and '{}'!\n"
                  "Only notifying on the first {} commits."
                  .format(__file__, len(revs), oldrev, newrev, len(revs)))
            break


class ParsedPatch:
    """
    Parse a patch into its parts.
    """

    header = {}
    diff = []

    def _insert_header(self, tag, line):
        line += '\n'
        if tag in self.header:
            self.header[tag] += line
        else:
            self.header.update({tag: line})


    def _parse_header(self, patch):
        lines = patch.split('\n')
        self.header['intro'] = lines[0].split('From ')[1]
        self.header['from'] = lines[1].split('From: ')[1]
        self.header['date'] = lines[2].split('Date: ')[1]
        self.header['title'] = lines[3].split('Subject: ')[1][8:] # skip leading "[PATCH] "
        # line after title is empty
        startline = 4
        while not lines[startline] == '':
            self.header['title'] += lines[startline]
            startline += 1
        startline += 1 # skip empty line

        tag = 'log'
        for line in lines[startline:]:
            if not line:
                tag = 'diff'
                continue
            if tag == 'log' and line[0:3] == '---':
                tag = 'stat'
            else:
                self._insert_header(tag, line)

        if 'log' not in self.header:
            self.header['log'] = ''
        return self.header['diff']


    def _parse_diff(self, diff):
        self.diff = PatchSet(diff)


    def __init__(self, patch):
        remain = self._parse_header(patch)
        print("::debug file={}:: header:'{}'"
              .format(__file__, json.dumps(self.header, indent=2)))
        self._parse_diff(remain)
        print("::debug file={}:: diff:'{}'"
              .format(__file__, str(self.diff)))


def format_stat(stat):
    """
    Format the patch-header statistics.
    """
    ret = ''
    fileno = 0
    lines = stat.split('\n')
    ret = '<table class="gn-statfile">'
    for line in lines:
        if not ' | ' in line:
            # last line if "3 files changed..."
            ret += '</table>' + line
            return ret
        (file, mod) = line.split(' | ')
        ret += '<tr><td><a href="#f{}">'.format(fileno) + file + '</a></td><td>'
        (num, plusminus) = mod.strip().split(' ')
        pluses = plusminus.count('+')
        minuses = plusminus.count('-')
        ret += ' ' + str(num) + '</td><td><span class="gn-statadd">' + '+' * pluses\
            + '</span><span class="gn-statrm">' + '-' * minuses + '</span></td></tr>'
        fileno += 1
    return '</table>' + ret


def format_spaces(escline):
    """
    Visualize leading and trailing spaces and tabs.
    """
    lspstrpline = escline.lstrip(' \t')
    repl = {' ': '&#xB7;', '\t': '&rarr;'}
    if len(escline) != len(lspstrpline):
        spaces = '<span class="gn-sp">'
        for char in escline[0:len(escline) - len(lspstrpline)]:
            spaces += repl[char]
        escline = spaces + '</span>' + lspstrpline
    rspstrpline = escline.rstrip(' \t')
    if len(escline) != len(rspstrpline):
        spaces = '<span class="gn-sp">'
        for char in escline[len(escline) - len(rspstrpline):]:
            spaces += repl[char]
        escline = rspstrpline + spaces + '</span>'
    return escline


def format_line(line):
    """
    Format a diff line.
    """
    escline = html.escape(str(line.value))
    escline = format_spaces(escline)
    linetypename = {'+' : 'plus', '-' : 'minus', ' ' : 'none'}
    substdiff = '    <tr class="gn-line">\n'\
        '      <td class="gn-slineno">'\
        + (html.escape(str(line.source_line_no)) if line.source_line_no else '')\
        + '</td><td class="gn-tlineno">'\
        + (html.escape(str(line.target_line_no)) if line.target_line_no else '')\
        + '</td><td class="gn-linetype">'\
        + html.escape(str(line.line_type))\
        + '</td><td class="gn-linestr gn-line'\
        + linetypename[html.escape(str(line.line_type))] + '">'\
        + escline\
        + '</td>\n'\
        '      </tr>'
    return substdiff


def format_hunk(hunk):
    """
    Format a hunk, by formatting its lines in a table.
    """
    substdiff = '    <table class="gn-hunk">\n'
    for line in hunk:
        substdiff += format_line(line)
        if len(substdiff) > 5000:
            substdiff += '<tr><td>...</td><td></td><td></td><td></td></tr>\n'
            break
    substdiff += '    </table>\n'
    return substdiff


def format_file(file, fileno):
    """
    Format a file section of a diff.
    """
    substdiff = '  <div class="gn-filediff"><a id="f' + str(fileno) +'"></a>\n'
    substdiff += '    <div class="gn-filename">'\
        + html.escape(file.path) + '</div>\n'
    # Earlier versions of unidiff don't register is_binary_file:
    if vars(PatchedFile).get('is_binary_file') and file.is_binary_file:
        substdiff += '    <div class="gn-binary">(binary file)</div>\n'
    else:
        for hunk in file:
            substdiff += format_hunk(hunk)
            if len(substdiff) > 5000:
                substdiff += '<div>... (too many hunks.)</div>\n'
                break
    substdiff += '  </div>\n'
    return substdiff


def get_patch(repo, ref, rev):
    """
    Get the html patch for the given commit.
    """
    commiturl = 'https://github.com/' + repo + '/commit/' + rev + '.patch'
    print("::debug file={}:: commit URL='{}'".format(__file__, commiturl))
    patch_set = None
    with urllib.request.urlopen(commiturl) as response:
        patch_set = ParsedPatch(response.read().decode('utf-8'))

    template_url = environ['INPUT_TEMPLATE']
    template_src = None
    if template_url.startswith('http'):
        with urllib.request.urlopen(template_url) as req:
            template_src = req.read().decode('utf-8')
    else:
        with open(template_url) as file:
            template_src = file.read()

    print("::debug file={}:: template='{}'".format(__file__, template_src))

    template = Template(template_src)
    subst = {
        'sha' : patch_set.header['intro'].split(' ')[0],
        'title': patch_set.header['title'],
        'from': patch_set.header['from'],
        'date': patch_set.header['date'],
        'log': patch_set.header['log'],
        'stat': patch_set.header['stat'],
        'repo': repo,
        'branch': ref.replace("refs/heads/", ""),
        'diff': ''
    }
    for key in subst:
        subst[key] = html.escape(subst[key])
    subst['stat'] = format_stat(subst['stat'])
    subst['log'] = subst['log'].replace('\n', '<br>')
    subst['stat'] = subst['stat'].replace('\n', '<br>')

    substdiff = '<div class="gn-diff">\n'
    fileno = 0
    for file in patch_set.diff:
        substdiff += format_file(file, fileno)
        fileno += 1
        if fileno > 20:
            substdiff += '<div>... (too many files.)</div>\n'
            break
    substdiff += '</div>\n'
    subst['diff'] = substdiff
    return (patch_set.header['title'], template.substitute(subst))


def send_html(subject, body):
    """
    Email the diff.
    """
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = environ['INPUT_FROM']
    message["To"] = environ['INPUT_TO']
    # Turn these into plain/html MIMEText objects
    part1 = MIMEText("Better with HTML!", "plain")
    part2 = MIMEText(body, "html")
    # Add HTML/plain-text parts to MIMEMultipart message
    # The email client will try to render the last part first
    message.attach(part1)
    message.attach(part2)

    # Create secure connection with server and send email
    print("::debug file={}:: connecting to {}@{}:{}"
          .format(__file__, environ['INPUT_LOGIN'], environ['INPUT_SMTP'], environ['INPUT_PORT']))
    with smtplib.SMTP(environ['INPUT_SMTP'], environ['INPUT_PORT']) as server:
        server.ehlo()  # Can be omitted
        context = ssl.create_default_context()
        server.starttls(context=context)
        server.ehlo()  # Can be omitted
        server.login(environ['INPUT_LOGIN'], environ['INPUT_PASSWORD'])
        server.sendmail(
            message["From"], message["To"], message.as_string()
        )

def format_subject(subject):
    """
    Generate a nice looking email subject.
    """
    return '[' + REPOSITORYNAME.split('/')[1] + '] ' + subject

REPOSITORYNAME = environ['GITHUB_REPOSITORY']
REF = environ['GITHUB_REF']
REF = REF.replace('/', '@')
NEWREV = environ['GITHUB_SHA']
OLDREV = ""

try:
    GH = get_github()
    print("::debug file={}:: GH='{}'".format(__file__, str(GH)))
    OLDREV = bump_rev_in_gist_and_get_old_rev(GH, REPOSITORYNAME, REF, NEWREV)
    print("::debug file={}:: OLDREV='{}'".format(__file__, str(OLDREV)))
    if OLDREV == NEWREV:
        print("warning:: file={}:: Notification was already sent for the \"new\" commit {}!\n"
              "Giving up happily."
              .format(__file__, NEWREV))
        exit(0)
    REPOSITORY = GH.repository(REPOSITORYNAME.split('/')[0], REPOSITORYNAME.split('/')[1])
    print("::debug file={}:: REPOSITORY='{}'".format(__file__, str(REPOSITORY)))
    REVS = collect_revs(REPOSITORY, OLDREV, NEWREV)
    print("::debug file={}:: REVS='{}'".format(__file__, str(REVS)))
except GitHubError as error:
    print("error:: file={}:: Github error:\n{}".format(__file__, error.errors))
    exit(1)

for REV in REVS:
    (title, html) = get_patch(REPOSITORYNAME, environ['GITHUB_REF'], REV)
    #with open("sample.html", "w") as outfile:
    #    outfile.write('<!DOCTYPE html>\n' + html)
    subj = format_subject(title)
    send_html(subj, html)