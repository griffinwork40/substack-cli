#!/usr/bin/env python3
"""Prop sprite library for the Goblin Files cover system.

Pulled out of pixel_studio.py when that file hit the 350-line ceiling. Each
prop is the per-post focal silhouette; palette chars are defined by PAL in
pixel_studio. Adding a post means adding a sprite here, not widening the engine.
"""

FLAGPOLE = ["b.kkwwkkww", "b.wwkkwwkk", "b.kkwwkkww", "b.wwkkwwkk",
            "b.........", "b.........", "b.........", "b........."]
MAGNIFIER = [".ssss..", "s....s.", "s.ee.s.", "s.ee.s.", "s....s.",
             ".ssss..", "....sss", ".....ss"]
BRIEFCASE = ["..kkkkk..", ".k.....k.", "kkkkkkkkk", "ksssssssk",
             "ksseeessk", "ksssssssk", "kkkkkkkkk"]
HOUSE = ["...d...", "..ddd..", ".ddddd.", "ddddddd", "d.eee.d", "d.eee.d", "ddddddd"]
PUZZLE = ["..pp..", ".pppp.", "pppppp", "pp..pp", "p....p", "pp..pp", "pppppp", ".pppp."]
PALM = ["..d.d.d..", ".ddd.ddd.", "dd.ddd.dd", "...bbb...", "....b....",
        "....b....", "...b.....", "..bb....."]
FILECAB = ["...www...", "..wwwww..", "kkkkkkkkk", "ksssssssk", "ks.eee.sk",
           "ksssssssk", "kkkkkkkkk", "ksssssssk", "ks.eee.sk", "ksssssssk", "kkkkkkkkk"]
FOLDER  = ["..yyy....", "oooooooo.", "owwwwwwo.", "oooooooo.", "oooooooo.", "oooooooo."]
# post 8: an itemised receipt. Last line (the total) sits in the accent colour.
RECEIPT = ["wwwwwwwww", "w.......w", "w.kkkkk.w", "w.......w", "w.kkk...w",
           "w.......w", "w.kkkkk.w", "w.......w", "w.kkk...w", "w.......w",
           "w.eeeee.w", "w.......w", "w.w.w.w.w"]

# post 9: the automated traffic that downloads every published version, and
# the package it comes for.
ROBOT = ["s...s", ".sss.", "seese", "sssss", ".sss.", "s...s"]
# a flat gold rectangle reads as a sticky note. The dark outline plus the tape
# cross is what makes it read as a shipping box at thumbnail size.
CRATE = ["kkkkkkkkk",
         "koooyoook",
         "koooyoook",
         "kyyyyyyyk",
         "koooyoook",
         "koooyoook",
         "kkkkkkkkk"]

# post 10: a wrench — the tool the goblin built for itself.
WRENCH = ["..yyy..", ".y...y.", "yy...yy", ".yyyyy.", "...y...", "...y...",
          "...y...", "..yyy..", ".y...y.", ".yyyyy."]

# post 11: personal infrastructure workshop tools
WORKBENCH = [
    "sssssssss",   # steel edge strip along the top
    "kkkkkkkkk",   # dark wood surface, top plank
    "kkkkkkkkk",   # dark wood surface, bottom plank
    "b.......b",   # open space between the two legs
    "b.......b",
    "bb.....bb",   # splayed feet for stability
]
HAMMER = [
    "kkkk...",   # top of hammer head
    "kkkkks.",   # claw notch right side
    "kkkk...",   # bottom of hammer head
    "..k....",   # neck
    "..b....",   # handle top
    "..b....",
    "..b....",
    "..b....",
    ".bbb...",   # grip flare at the base
]
