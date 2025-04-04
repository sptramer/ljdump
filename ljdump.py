#!/usr/bin/python
#
# ljdump.py - livejournal archiver
# Greg Hewgill <greg@hewgill.com> https://hewgill.com/
# Version 1.5.1
#
# LICENSE
#
# This software is provided 'as-is', without any express or implied
# warranty.  In no event will the author be held liable for any damages
# arising from the use of this software.
#
# Permission is granted to anyone to use this software for any purpose,
# including commercial applications, and to alter it and redistribute it
# freely, subject to the following restrictions:
#
# 1. The origin of this software must not be misrepresented; you must not
#    claim that you wrote the original software. If you use this software
#    in a product, an acknowledgment in the product documentation would be
#    appreciated but is not required.
# 2. Altered source versions must be plainly marked as such, and must not be
#    misrepresented as being the original software.
# 3. This notice may not be removed or altered from any source distribution.
#
# Copyright (c) 2005-2010 Greg Hewgill and contributors

import argparse
import codecs
import os
import pickle
import pprint
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
import xml.dom.minidom
import xmlrpc.client
from xml.sax import saxutils

MimeExtensions = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}

from hashlib import md5

def calcchallenge(challenge, password):
    return md5(challenge.encode("UTF-8")+md5(password.encode("UTF-8")).hexdigest().encode("UTF-8")).hexdigest()

def flatresponse(response):
    r = {}
    while True:
        name = str(response.readline(), "UTF-8")
        if len(name) == 0:
            break
        if name[-1] == '\n':
            name = name[:len(name)-1]
        value = str(response.readline(), "UTF-8")
        if value[-1] == '\n':
            value = value[:len(value)-1]
        r[name] = value
    return r

def getljsession(server, username, password):
    r = urllib.request.urlopen(server+"/interface/flat", b"mode=getchallenge")
    response = flatresponse(r)
    r.close()
    r = urllib.request.urlopen(server +"/interface/flat",
                               "mode=sessiongenerate&user={}&auth_method=challenge&auth_challenge={}&auth_response={}".format(
                                   username, response['challenge'], calcchallenge(response['challenge'], password)).encode("UTF-8"))
    response = flatresponse(r)
    r.close()
    return response['ljsession']

def dochallenge(server, params, password):
    challenge = server.LJ.XMLRPC.getchallenge()
    params.update({
        'auth_method': "challenge",
        'auth_challenge': challenge['challenge'],
        'auth_response': calcchallenge(challenge['challenge'], password)
    })
    return params

def dumpelement(f, name, e):
    def wrap(input, encoding = "UTF-8"):
        if isinstance(input, "".__class__):
            return input
        if isinstance(input, bytes().__class__):
            return str(input, encoding)
        return str(input)

    f.write("<%s>\n" % name)
    for k in list(e.keys()):
        if isinstance(e[k], {}.__class__):
            dumpelement(f, k, e[k])
        else:
            try:
                s = wrap(e[k])
            except UnicodeDecodeError:
                # fall back to Latin-1 for old entries that aren't UTF-8
                s = wrap(e[k], "cp1252")
            f.write("<%s>%s</%s>\n" % (k, saxutils.escape(s), k))
    f.write("</%s>\n" % name)

def writedump(fn, event):
    f = codecs.open(fn, "w", "UTF-8")
    f.write("""<?xml version="1.0"?>\n""")
    dumpelement(f, "event", event)
    f.close()

def writelast(journal, lastsync, lastmaxid):
    f = open("%s/.last" % journal, "w")
    f.write("%s\n" % lastsync)
    f.write("%s\n" % lastmaxid)
    f.close()

def createxml(doc, name, map):
    e = doc.createElement(name)
    for k in list(map.keys()):
        me = doc.createElement(k)
        me.appendChild(doc.createTextNode(map[k]))
        e.appendChild(me)
    return e

def gettext(e):
    if len(e) == 0:
        return ""
    return e[0].firstChild.nodeValue

def ljdump(Server, Username, Password, Journal, verbose=True):
    m = re.search("(.*)/interface/xmlrpc", Server)
    if m:
        Server = m.group(1)
    if Username != Journal:
        authas = "&authas=%s" % Journal
    else:
        authas = ""

    if verbose:
        print(("Fetching journal entries for: %s" % Journal))
    try:
        os.mkdir(Journal)
        print("Created subdirectory: %s" % Journal)
    except:
        pass

    ljsession = getljsession(Server, Username, Password)

    server = xmlrpc.client.ServerProxy(Server+"/interface/xmlrpc")

    newentries = 0
    newcomments = 0
    errors = 0

    lastsync = ""
    lastmaxid = 0
    try:
        f = open("%s/.last" % Journal, "r")
        lastsync = f.readline()
        if lastsync[-1] == '\n':
            lastsync = lastsync[:len(lastsync)-1]
        lastmaxid = f.readline()
        if len(lastmaxid) > 0 and lastmaxid[-1] == '\n':
            lastmaxid = lastmaxid[:len(lastmaxid)-1]
        if lastmaxid == "":
            lastmaxid = 0
        else:
            lastmaxid = int(lastmaxid)
        f.close()
    except:
        pass
    origlastsync = lastsync

    r = server.LJ.XMLRPC.login(dochallenge(server, {
        'username': Username,
        'ver': 1,
        'getpickws': 1,
        'getpickwurls': 1,
    }, Password))
    userpics = dict(list(zip(list(map(str, r['pickws'])), r['pickwurls'])))
    if r['defaultpicurl']:
        userpics['*'] = r['defaultpicurl']

    while True:
        r = server.LJ.XMLRPC.syncitems(dochallenge(server, {
            'username': Username,
            'ver': 1,
            'lastsync': lastsync,
            'usejournal': Journal,
        }, Password))
        #pprint.pprint(r)
        if len(r['syncitems']) == 0:
            break
        for item in r['syncitems']:
            if item['item'][0] == 'L':
                print("Fetching journal entry %s (%s)" % (item['item'], item['action']))
                try:
                    e = server.LJ.XMLRPC.getevents(dochallenge(server, {
                        'username': Username,
                        'ver': 1,
                        'selecttype': "one",
                        'itemid': item['item'][2:],
                        'usejournal': Journal,
                    }, Password))
                    if e['events']:
                        writedump("%s/%s" % (Journal, item['item']), e['events'][0])
                        newentries += 1
                    else:
                        print("Unexpected empty item: %s" % item['item'])
                        errors += 1
                except xmlrpc.client.Fault as x:
                    print("Error getting item: %s" % item['item'])
                    pprint.pprint(x)
                    errors += 1
            lastsync = item['time']
            writelast(Journal, lastsync, lastmaxid)

    # The following code doesn't work because the server rejects our repeated calls.
    # https://www.livejournal.com/doc/server/ljp.csp.xml-rpc.getevents.html
    # contains the statement "You should use the syncitems selecttype in
    # conjuntions [sic] with the syncitems protocol mode", but provides
    # no other explanation about how these two function calls should
    # interact. Therefore we just do the above slow one-at-a-time method.

    #while True:
    #    r = server.LJ.XMLRPC.getevents(dochallenge(server, {
    #        'username': Username,
    #        'ver': 1,
    #        'selecttype': "syncitems",
    #        'lastsync': lastsync,
    #    }, Password))
    #    pprint.pprint(r)
    #    if len(r['events']) == 0:
    #        break
    #    for item in r['events']:
    #        writedump("%s/L-%d" % (Journal, item['itemid']), item)
    #        newentries += 1
    #        lastsync = item['eventtime']

    if verbose:
        print(("Fetching journal comments for: %s" % Journal))

    try:
        f = open("%s/comment.meta" % Journal, "rb")
        metacache = pickle.load(f)
        f.close()
    except:
        metacache = {}

    try:
        f = open("%s/user.map" % Journal, "rb")
        usermap = pickle.load(f)
        f.close()
    except:
        usermap = {}

    maxid = lastmaxid
    while True:
        try:
            try:
                r = urllib.request.urlopen(urllib.request.Request(Server+"/export_comments.bml?get=comment_meta&startid=%d%s" % (maxid+1, authas), headers = {'Cookie': "ljsession="+ljsession}))
                meta = xml.dom.minidom.parse(r)
            except Exception as x:
                print("*** Error fetching comment meta, possibly not community maintainer?")
                print("***", x)
                break
        finally:
            try:
                r.close()
            except AttributeError: # r is sometimes a dict for unknown reasons
                pass
        for c in meta.getElementsByTagName("comment"):
            id = int(c.getAttribute("id"))
            metacache[id] = {
                'posterid': c.getAttribute("posterid"),
                'state': c.getAttribute("state"),
            }
            if id > maxid:
                maxid = id
        for u in meta.getElementsByTagName("usermap"):
            usermap[u.getAttribute("id")] = u.getAttribute("user")
        if maxid >= int(meta.getElementsByTagName("maxid")[0].firstChild.nodeValue):
            break

    f = open("%s/comment.meta" % Journal, "wb")
    pickle.dump(metacache, f)
    f.close()

    f = open("%s/user.map" % Journal, "wb")
    pickle.dump(usermap, f)
    f.close()

    newmaxid = maxid
    maxid = lastmaxid
    while True:
        try:
            try:
                r = urllib.request.urlopen(urllib.request.Request(Server+"/export_comments.bml?get=comment_body&startid=%d%s" % (maxid+1, authas), headers = {'Cookie': "ljsession="+ljsession}))
                meta = xml.dom.minidom.parse(r)
            except Exception as x:
                print("*** Error fetching comment body, possibly not community maintainer?")
                print("***", x)
                break
        finally:
            r.close()
        for c in meta.getElementsByTagName("comment"):
            id = int(c.getAttribute("id"))
            jitemid = c.getAttribute("jitemid")
            comment = {
                'id': str(id),
                'parentid': c.getAttribute("parentid"),
                'subject': gettext(c.getElementsByTagName("subject")),
                'date': gettext(c.getElementsByTagName("date")),
                'body': gettext(c.getElementsByTagName("body")),
                'state': metacache[id]['state'],
            }
            if c.getAttribute("posterid") in usermap:
                comment["user"] = usermap[c.getAttribute("posterid")]
            try:
                entry = xml.dom.minidom.parse("%s/C-%s" % (Journal, jitemid))
            except:
                entry = xml.dom.minidom.getDOMImplementation().createDocument(None, "comments", None)
            found = False
            for d in entry.getElementsByTagName("comment"):
                if int(d.getElementsByTagName("id")[0].firstChild.nodeValue) == id:
                    found = True
                    break
            if found:
                print("Warning: downloaded duplicate comment id %d in jitemid %s" % (id, jitemid))
            else:
                entry.documentElement.appendChild(createxml(entry, "comment", comment))
                f = codecs.open("%s/C-%s" % (Journal, jitemid), "w", "UTF-8")
                entry.writexml(f)
                f.close()
                newcomments += 1
            if id > maxid:
                maxid = id
        if maxid >= newmaxid:
            break

    lastmaxid = maxid

    writelast(Journal, lastsync, lastmaxid)

    if Username == Journal:
        if verbose:
            print(("Fetching userpics for: %s" % Username))
        f = open("%s/userpics.xml" % Username, "w")
        print("""<?xml version="1.0"?>""", file=f)
        print("<userpics>", file=f)
        for p in userpics:
            print("""<userpic keyword="%s" url="%s" />""" % (p, userpics[p]), file=f)
            pic = urllib.request.urlopen(userpics[p])
            ext = MimeExtensions.get(pic.info()["Content-Type"], "")
            picfn = re.sub(r'[*?\\/:<>"|]', "_", p)
            try:
                picfn = codecs.utf_8_decode(picfn)[0]
                picf = open("%s/%s%s" % (Username, picfn, ext), "wb")
            except:
                # for installations where the above utf_8_decode doesn't work
                picfn = "".join([ord(x) < 128 and x or "_" for x in picfn])
                picf = open("%s/%s%s" % (Username, picfn, ext), "wb")
            shutil.copyfileobj(pic, picf)
            pic.close()
            picf.close()
        print("</userpics>", file=f)
        f.close()

    if verbose or (newentries > 0 or newcomments > 0):
        if origlastsync:
            print(("%d new entries, %d new comments (since %s)" % (newentries, newcomments, origlastsync)))
        else:
            print(("%d new entries, %d new comments" % (newentries, newcomments)))
    if errors > 0:
        print("%d errors" % errors)

if __name__ == "__main__":
    args = argparse.ArgumentParser(description="Livejournal archive utility")
    args.add_argument("--quiet", "-q", action='store_false', dest='verbose',
                      help="reduce log output")
    args = args.parse_args()
    if os.access("ljdump.config", os.F_OK):
        config = xml.dom.minidom.parse("ljdump.config")
        server = config.documentElement.getElementsByTagName("server")[0].childNodes[0].data
        username = config.documentElement.getElementsByTagName("username")[0].childNodes[0].data
        password = config.documentElement.getElementsByTagName("password")[0].childNodes[0].data
        journals = config.documentElement.getElementsByTagName("journal")
        if journals:
            for e in journals:
                ljdump(server, username, password, e.childNodes[0].data, args.verbose)
        else:
            ljdump(server, username, password, username, args.verbose)
    else:
        from getpass import getpass
        print("ljdump - livejournal archiver")
        print()
        default_server = "https://livejournal.com"
        server = input("Alternative server to use (e.g. 'https://www.dreamwidth.org'), or hit return for '%s': " % default_server) or default_server
        print()
        print("Enter your Livejournal username and password.")
        print()
        username = input("Username: ")
        password = getpass("Password: ")
        print()
        print("You may back up either your own journal, or a community.")
        print("If you are a community maintainer, you can back up both entries and comments.")
        print("If you are not a maintainer, you can back up only entries.")
        print()
        journal = input("Journal to back up (or hit return to back up '%s'): " % username)
        print()
        if journal:
            ljdump(server, username, password, journal, args.verbose)
        else:
            ljdump(server, username, password, username, args.verbose)
# vim:ts=4 et:	
