#!/usr/bin/env python3.11
import os
import requests
import jinja2
import praw
import prawcore
from datetime import datetime


def get_table(url):
    dataset = requests.get(url).json()

    table = []
    for ds in dataset:
        entry = {}
        entry['name'] = ds['teamName']
        entry['points'] = ds['points']
        table.append(entry)

    return table


def get_gameplan(url):
    dataset = requests.get(url).json()
    gameplan = []
    for ds in dataset['all']:
        entry = {}
        entry['date'] = ds['datum']
        entry['time'] = ds['uhrzeit']
        entry['home'] = ds['heim']
        entry['away'] = ds['gast']
        if not ds['heimTore'] is None:
            entry['res'] = str(ds['heimTore']) + ':' + str(ds['gastTore'])
        entry['league'] = (ds['league'] == dataset['league'][0]['league'])

        if not entry.get('res'):
            entry['res'] = "-:-"
        else:
            a = ds['heimTore']
            b = ds['gastTore']
            res = ''
            if a == b:
                res = 'D'
            elif entry['home'] == 'GAK 1902':
                res = 'W' if a > b else 'L'
            else:
                res = 'L' if a > b else 'W'
            entry['res'] = res + ' (' + entry['res'] + ')'

        gameplan.append(entry)
    return gameplan


def get_next_games(gameplan):
    next_games = []
    for e in gameplan:
        dt = datetime.strptime(e['date'], "%d.%m.%Y").date()
        now = datetime.now().date()
        if dt >= now:
            # show todays game too
            next_games.append(e)
        if len(next_games) >= 2:
            break

    return next_games


def pub_sidebar(subreddit, content):
    subreddit.wiki["config/sidebar"].edit(content=content)


def update_gp_post(reddit, subreddit, title, content):
    post = None
    for i in range(4):
        try:
            chk = subreddit.sticky(number=i)
            if chk.title == title and chk.author == reddit.user.me():
                post = chk
                break
        except prawcore.exceptions.NotFound:
            break
    if not post:
        post = subreddit.submit(title, selftext="placeholder")
    post.edit(content)
    post.mod.sticky(bottom=False, state=True)


def get_gp_title(gameplan):
    title = "Spielplan "
    title += datetime.strftime(datetime.strptime(gameplan[0]['date'],
                                                 "%d.%m.%Y"),
                               "%Y")
    title += datetime.strftime(datetime.strptime(gameplan[-1]['date'],
                                                 "%d.%m.%Y"),
                               "/%y")
    return title


def main():
    reddit = praw.Reddit(user_agent="GAK mod")
    reddit.validate_on_submit = True
    subreddit = reddit.subreddit("grazerak")

    cur_path = os.path.dirname(os.path.abspath(__file__)) + '/'
    templ_path = cur_path + "templates/"
    jenv = jinja2.Environment(
        loader=jinja2.FileSystemLoader(templ_path))
    sidebar_tmpl = jenv.get_template("sidebar.tmpl")
    timestamp = datetime.strftime(datetime.now(), "%Y-%m-%d %H:%M")
    sidebar_tmpl.globals['timestamp'] = timestamp
    gameplan_tmpl = jenv.get_template("gameplan.tmpl")
    gameplan_tmpl.globals['timestamp'] = timestamp
    table_url = "https://www.grazerak.at/api/table/117"
    table = get_table(table_url)

    gameplan_url = "https://www.grazerak.at/api/fixtures/0/1"
    try:
        gameplan = get_gameplan(gameplan_url)
    except requests.exceptions.JSONDecodeError:
        return -1
    next_games = get_next_games(gameplan)

    content = sidebar_tmpl.render(table=table, next_games=next_games)
    with open(cur_path + "output/sidebar.txt", 'w') as f:
        f.write(content)
    pub_sidebar(subreddit, content)

    content = gameplan_tmpl.render(gameplan=gameplan)
    with open(cur_path + "output/gameplan.txt", 'w') as f:
        f.write(content)
    gp_title = get_gp_title(gameplan)
    update_gp_post(reddit, subreddit, gp_title, content)

    return


if __name__ == "__main__":
    main()
