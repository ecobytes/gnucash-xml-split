#!/usr/bin/env python3

# for python 2.x
#from __future__ import print_function

import argparse
import decimal
import gzip
import datetime
import uuid
from collections import defaultdict
from xml.etree import ElementTree

tzoff = None
nl = "\n"

nsdict = {}
for ns in ["gnc", "cd", "book", "slot", "cmdty", "price", "ts", "act", "trn", "split", "recurrence", "cust", "bgt", "addr", "vendor", "sx"]:
    url = "http://www.gnucash.org/XML/" + ns
    ElementTree.register_namespace(ns, url)
    nsdict[ns] = url

class Account(object):
    def __init__(self, name, guid, actype, parent=None,
    commodity=None, description=None, name_full=None):
        self.name = name
        self.guid = guid
        self.actype = actype
        self.description = description
        self.parent = parent
        self.commodity = commodity
        self.name_full = name_full
        self.balance = decimal.Decimal(0)
    def __repr__(self):
        return "<Account {}>".format(self.guid)
    def find_account(self, name):
        for account, children, splits in self.walk():
            if account.name == name:
                return account

def is_gzip(fn):
    f = open(fn, 'rb')
    s = f.read(2)
    f.close()
    return s == b'\x1f\x8b'

def ns_tag(nstag):
    ns, tag = nstag.split(':')
    return '{' + nsdict[ns] + '}' + tag

def new_element(tag, text=None, tail=nl):
    el = ElementTree.Element(ns_tag(tag))
    el.text = text
    el.tail = tail
    return el


parser = argparse.ArgumentParser(description='GnuCash XML Splitter')
parser.add_argument('-i','--input',  help='input file name',required=True)
parser.add_argument('-o','--output', help='output file name', required=True)
parser.add_argument('-y','--year',   help='begin year of transations to extract', required=True)
parser.add_argument('-e','--end-year',help='end year of transactions', default='2442')
parser.add_argument('-n','--nozip',  help='do not zip output files', action='store_true')
parser.add_argument('-z','--zero',   help='zero starting balances in extracted file', action='store_true')
parser.add_argument('-q','--qif',    help='write starting balances to .qif file', action='store_true')

args = parser.parse_args()

dtfrom = datetime.datetime.strptime(args.year + "-01-01 00:00:00", '%Y-%m-%d %H:%M:%S')
dtto   = datetime.datetime.strptime(args.end_year + "-12-31 23:59:59", '%Y-%m-%d %H:%M:%S')

if is_gzip(args.input):
    xmltree = ElementTree.parse(gzip.open(args.input, "rb"))
else:
    xmltree = ElementTree.parse(args.input)

root = xmltree.getroot()
if root.tag != 'gnc-v2':
    raise ValueError("File stream was not a valid GNU Cash v2 XML file")
root.tail = nl

book_count = 0
for book in root.findall('./gnc:book', nsdict):
    book_count += 1

    # create accountdict
    accountdict = {}
    for account in book.findall('gnc:account', nsdict):
        name = account.find('act:name', nsdict).text
        guid = account.find('act:id', nsdict).text
        actype = account.find('act:type', nsdict).text
        description = account.find('act:description', nsdict)
        if description is not None:
            description = description.text
        if actype == 'ROOT':
            parent = None
            commodity = None
        else:
            parent = account.find('act:parent', nsdict).text
            commodity = account.find('act:commodity/cmdty:id', nsdict).text
        accountdict[guid] = Account(name=name,
                description=description,
                guid=guid,
                parent=parent,
                actype=actype,
                commodity=commodity)

    # add names to subaccounts
    for acc_key in accountdict:
        account = accountdict[acc_key]
        if account.actype == 'ROOT':
            account.name_full = ''
        else:
            ancestor = [account, accountdict[account.parent]]
            while len(ancestor) > 1:
                ances = ancestor[-1];
                curr = ancestor[-2];
                if ances.actype == 'ROOT':
                    curr.name_full = curr.name
                    ancestor.pop()
                elif ances.name_full is not None:
                    curr.name_full = ances.name_full + ':' + curr.name
                    ancestor.pop()
                else:
                    ancestor.append(accountdict[ances.parent])

    # look at every transaction
    keep_count = 0
    prior_count = 0
    later_count = 0
    balance_count = 0
    for transaction in book.findall('./gnc:transaction', nsdict):
        datestr = transaction.find('./trn:date-posted/ts:date', nsdict).text
        # '%z' is only in python 3.x
        dt = datetime.datetime.strptime(datestr, '%Y-%m-%d %H:%M:%S %z').replace(tzinfo=None)
        # for python 2.x
        #dt = datetime.datetime.strptime(' '.join(datestr.split()[:2]), '%Y-%m-%d %H:%M:%S')
        if not tzoff:
            tzoff = datestr.split()[2]
        if dt >= dtfrom and dt <= dtto:
            # count transactions to be extracted
            keep_count += 1
        else:
            if dt < dtfrom:
                prior_count += 1
                # for prior transactions, accumulate starting balance
                for split in transaction.findall('trn:splits/trn:split', nsdict):
                    quantity = split.find("split:quantity", nsdict).text
                    account = accountdict[split.find("split:account", nsdict).text]
                    num, denom = quantity.split("/")
                    account.balance = account.balance + decimal.Decimal(num) / decimal.Decimal(denom)
            else:
                later_count += 1

            # remove all transactions not in range
            book.remove(transaction)

    # reset count-data of transactions to the number in extracted portion
    for count in book.findall('gnc:count-data', nsdict):
        if count.get(ns_tag('cd:type')) == 'transaction':
            count.text = str(keep_count)

    if not args.zero and prior_count > 0:
        if args.qif:
            # create balancedict for starting balances
            balancedict = defaultdict(list)
            for acc_key in accountdict:
                account = accountdict[acc_key]
                if account.actype != 'ROOT' and account.actype != 'INCOME' and account.actype != 'EXPENSE' and account.actype != 'EQUITY' and account.balance != 0:
                    balancedict[account.commodity].append(account)

            # write opening balances as .qif file
            for cmdy_key in balancedict:
                f = open(args.year + cmdy_key + '.qif', 'w')
                print('!Account', file=f)
                print('NEquity:Opening Balances:'+cmdy_key, file=f)
                print('TOth A', file=f)
                print('^', file=f)
                print('!Type:Oth A', file=f)
                print('D' + args.year + '-01-01', file=f)
                for account in balancedict[cmdy_key]:
                    balance_count += 1
                    print('S' + account.name_full, file=f)
                    print('$' + str(-account.balance), file=f)
                    print('^', file=f)
                f.close()
        else:
            # create new starting balance transaction
            tran = new_element('gnc:transaction', nl)
            tran.set('version', "2.0.0")
            tran_id = new_element('trn:id', str(uuid.uuid4()).replace('-',''))
            tran_id.set('type', "guid")
            tran.append(tran_id)
            tran_cur = new_element('trn:currency', nl)
            tran_cur.append(new_element('cmdty:space', "ISO4217"))
            tran_cur.append(new_element('cmdty:id', account.commodity))
            tran.append(tran_cur)
            posted = new_element('trn:date-posted', nl)
            posted.append(new_element('ts:date',
                                        str(int(args.year)-1) + "-12-31 00:00:00 " + tzoff))
            tran.append(posted)
            entered = new_element('trn:date-entered', nl)
            entered.append(new_element('ts:date',
                                        str(int(args.year)-1) + "-12-31 00:00:00 " + tzoff))
            tran.append(entered)
            tran.append(new_element('trn:description',
                                    "Balance as of 31 December " + str(int(args.year)-1)))
            splits = new_element('trn:splits', nl)
            for acc_key in accountdict:
                account = accountdict[acc_key]
                if account.balance != 0:
                    balance_count += 1
                    splt = new_element('trn:split', nl)
                    splt_id = new_element('split:id', str(uuid.uuid4()).replace('-',''))
                    splt_id.set('type', "guid")
                    splt.append(splt_id)
                    splt.append(new_element('split:reconciled-state', "n"))
                    denom = 10 ** len((str(account.balance)+'.').split('.')[1])
                    denom = denom if denom >= 100 else 100
                    amount = "%d/%d" % (int(account.balance * denom), denom)
                    splt.append(new_element('split:value', amount))
                    splt.append(new_element('split:quantity', amount))
                    splt_acc = new_element('split:account', acc_key)
                    splt_acc.set('type', "guid")
                    splt.append(splt_acc)
                    splits.append(splt)

            tran.append(splits)
            book.append(tran)

# write tree with only specified years remaining
if args.nozip:
    xmltree.write(args.output, encoding="utf-8", xml_declaration=True)
else:
    f = gzip.open(args.output, 'wb')
    xmltree.write(f, encoding="utf-8", xml_declaration=True)
    f.close()

# TODO: only reports last book
if book_count == 1:
    print("Wrote %d transactions to %s (%sgzipped)" % (
        keep_count, args.output, 'not ' if args.nozip else ''))
    print("Skipped %d prior transactions and %d later transactions" % (
        prior_count, later_count))
    if not args.zero and prior_count > 0:
        if not args.qif:
            print("Prior balance for %d accounts written as a starting balance transaction" % (
                balance_count))
        else:
            print("Prior balance for %d accounts written to %d*.qif" % (
                balance_count))
else:
    print("Processed %d books" % (book_count))

### end ###
