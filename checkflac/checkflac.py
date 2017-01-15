#!/usr/bin/env python

"""
Script to validate certain conventions of FLAC releases

It does checks at different level. For example, the tracktotal check
is done at the disc level. This means that the property has to be there,
but it also has to be the same for every track on that disc. Properties
at the album level have to be the same for every track in the album.

Checks:
 - The FLAC files:
   - checks that files aren't corrupted by verifying the STREAMINFO MD5
   - checks the path length of each file
   - [TODO] recommed re-encoding to add the md5 if one doesn't exist

 - The extra info:
   - checks if a cue and log file are provided at the disc level
   - checks a cover image is provided at the album level
   - checks if an m3u file shoudl be deleted
   - [TODO] check a folder with additional art is provided at the album level

 - The folder/file names:
   - [TODO] validate a naming scheme
     - folder: [<ALBUMARTIST> - ]<ALBUM> (<YEAR>) \[{CD,WEB}-FLAC\] {<anything}}
     - file: <TRACKNUMBER> - [<ARTIST> - ]<TITLE>.flac
   - [TODO] validate the name against vorbis information

 - vorbis information:
   - album, date, albumartist, and disctotal are at the album level
   - discnumber, tracktotal are at the disc level
   - artist, tracknumber, title are at the track level
   - checks for totaldiscs and totaltracks metadata
   - checks that disctotal is equal to the amount of discs
   - checks that tracktotal is equal to the number of tracks
   - checks for duplicate tags
   - check the COMPILATION tag
   - [TODO] warn if TRACKNUMBER is the "tracknum/totaltracks" style
   - [TODO] warn if album art is embedded

 - replaygain information:
   - checks reference loudness, album gain, album peak are at the disc level
   - checks track gain and track peak are at the track level
"""

import argparse
import enum
import functools
import itertools
import os
import subprocess

import taglib

MAX_PATH_LENGTH = 180
COVER_FILENAME = "cover.jpg"
VARIOUS_ARTISTS = "Various Artists"


def has_ext(path, ext):
    return path.rsplit(".", 1)[-1].lower() == ext.lower()


def files_by_ext(files, ext):
    return [x for x in files if has_ext(x, ext)]


def validator(func):
    """Calls the pre_validate and post_validate functions before and after the
    wrapped function"""
    @functools.wraps(func)
    def wrapped(self):
        self.pre_validate()
        func(self)
        self.post_validate()

    return wrapped


class Missing(enum.Enum):
    NONE = 0
    SOME = 1
    ALL = 2


class ValidatorBase(object):

    REQUIRED_TAGS = {}
    REPLAYGAIN_TAGS = {}

    def _check_all_same(self, tag):
        """Check and generate messages but don't print them

        returns (missing code, multiple, msg)
        """
        code = Missing.NONE
        multiple = False
        msgs = []

        temp = self.get_tag(tag, placeholder=True)
        tags = set(temp)
        if None in tags:
            tags.remove(None)
            if len(tags) == 0:
                code = Missing.ALL
                msgs.append("missing from all items")
            else:
                code = Missing.SOME
                msgs.append("missing from {}/{} items".format(temp.count(None),
                                                             len(temp)))
        if len(tags) > 1:
            multiple = True
            msgs.append("multiple values: {}".format(tags))

        return code, multiple, msgs

    def validate_all_same(self, tag):
        code, multiple, msgs = self._check_all_same(tag)

        # Special case hint for blank ALBUMARTIST
        if tag == "ALBUMARTIST" and isinstance(self, Album) and code == Missing.ALL:
            msgs[-1] += " (is this a compilation?)"

        if code != Missing.NONE or multiple:
            if isinstance(self, Track):
                # Track can't have multiple values
                print("Problem with tag {}: missing".format(tag))
            else:
                print("Problem with tag {}: {}".format(tag, ", ".join(msgs)))

    def validate_number_metadata(self):
        # Check for invalid [type]TOTAL metadata
        if isinstance(self, Album):
            tag = "DISC"
        elif isinstance(self, Disc):
            tag = "TRACK"
        else:
            # Nothing to do for tracks
            return

        total_bad_tag = "TOTAL{}S".format(tag)
        total_good_tag = "{}TOTAL".format(tag)
        number_tag = "{}NUMBER".format(tag)
        tag = tag.lower()

        # Check for the wrong tag information ([type]TOTAL > TOTAL[types]S)
        if self.get_tag(total_bad_tag):
            if self.get_tag(total_good_tag):
                print("{} tag(s) detected, delete them ({} tag already exists)"
                      "".format(total_bad_tag, total_good_tag))
            else:
                print("{} tag(s) detected, convert them to {} tags"
                      "".format(total_bad_tag, total_good_tag))

        # Check [type]TOTAL = number of [type]s
        temp = self.get_valid_tag(total_good_tag)
        if temp is not None:
            try:
                total = int(temp)
            except (ValueError, TypeError):
                print("Problem with {} tag (non-numeric)".format(total_good_tag))
            else:
                if total != len(self.children):
                    print("Problem with {0} tag (found {2} {1}s, {0}={3})"
                          "".format(total_good_tag, tag, len(self.children), total))

        # Check [type] sort order
        numbers = self.get_tag(number_tag)
        if numbers:
            try:
                numbers = [int(x) for x in numbers]
            except (ValueError, TypeError):
                print("WARNING: Not checking {} sort order ({} metadata is non-numeric)"
                      "".format(tag, number_tag))
            else:
                if sorted(numbers) != numbers:
                    print("{}s do not sort properly according to the {} metadata"
                          "".format(tag.title(), number_tag))

    def validate_metadata_structure(self):
        for tag in self.REQUIRED_TAGS:
            self.validate_all_same(tag)

    def validate_replaygain(self):
        # To fix replaygain: `metaflac --add-replay-gain <all files from disc>`
        for tag in self.REPLAYGAIN_TAGS:
            self.validate_all_same(tag)

    def pre_validate(self):
        if self.name is None:
            print("Validating the only {}".format(self.level))
        else:
            print("Validating {}: {}".format(self.level, self.name))

        self.validate_metadata_structure()
        self.validate_replaygain()
        self.validate_number_metadata()

    @validator
    def validate():
        pass

    def post_validate(self):
        if self.children is not None:
            for x in self.children:
                x.validate()

    def get_tag(self, tag_name, placeholder=False):
        if self.children is None:
            # No children to search through, return the tag
            if tag_name in self.tags:
                tag = self.tags[tag_name]

                # Check for duplicate tags
                num_tags = len(tag)
                if num_tags > 1:
                    print("Found {} '{}' tags: {}".format(num_tags, tag_name, tag))
                return [tag[0]]
            if placeholder:
                return [None]
            else:
                return []

        return list(itertools.chain.from_iterable(x.get_tag(tag_name, placeholder)
                                                  for x in self.children))

    def get_valid_tag(self, tag_name):
        """Get a tag's valid if all children have the same valid (otherwise None)"""
        tags = set(self.get_tag(tag_name))
        if len(tags) == 1:
            return next(iter(tags))
        return None

    @property
    def level(self):
        return self.__class__.__name__.lower()

    @property
    def children(self):
        if isinstance(self, Album):
            return self.discs
        elif isinstance(self, Disc):
            return self.tracks
        else:
            return None


class Album(ValidatorBase):

    REQUIRED_TAGS = {"ALBUM", "DATE", "ALBUMARTIST", "DISCTOTAL"}

    def __init__(self, directory):
        self.directory = os.path.abspath(directory)

        if not os.path.isdir(self.directory):
            raise FileNotFoundError("Directory '{}' does not exist".format(self.directory))

        self.parent_dir = os.path.dirname(self.directory)
        self.name = os.path.basename(self.directory)
        self.discs = self._find_discs()

    def validate_compilation(self):
        """Validate the relationship between ALBUMARTIST and COMPILATION"""
        compilation = self.get_valid_tag("COMPILATION")
        missing, multiple, _ = self._check_all_same("COMPILATION")
        if missing == Missing.SOME and (multiple or compilation != "1"):
            print("Invalid COMPILATION tag: must all be set to \"1\" or unset")
            return

        # At this point, the compilation tag is either not there or correct
        artist = self.get_valid_tag("ALBUMARTIST")
        if not compilation:
            if artist == VARIOUS_ARTISTS:
                print("ALBUMARTIST is '{}' but COMPILATION is not set".format(VARIOUS_ARTISTS))
        elif not artist:
            print("COMPILATION is set but ALBUMARTIST isn't - set ALBUMARTIST to '{}'?".format(VARIOUS_ARTISTS))

    @validator
    def validate(self):
        self.validate_compilation()

    def _find_discs(self):
        ret = []
        for dirpath, dirs, files in os.walk(self.directory):
            if not any(x for x in files if has_ext(x, "flac")):
                continue

            ret.append(Disc(self, dirpath, files))

        return sorted(ret, key=lambda x: x.name)


class Disc(ValidatorBase):

    REQUIRED_TAGS = {"DISCNUMBER", "TRACKTOTAL"}
    REPLAYGAIN_TAGS = {"REPLAYGAIN_REFERENCE_LOUDNESS", "REPLAYGAIN_ALBUM_GAIN",
                       "REPLAYGAIN_ALBUM_PEAK"}

    def __init__(self, album, directory, files):
        self.album = album
        self.directory = directory

        # Sort the files by name to later validate they sort correctly by tracknumber
        self.files = sorted(files)
        self.tracks = self._find_tracks()

        if directory != self.album.directory:
            self.name = os.path.basename(directory)
        else:
            self.name = None

    @validator
    def validate(self):
        # Check album art is present
        if COVER_FILENAME not in self.files:
            print("No cover art found (looking for '{}')".format(COVER_FILENAME))

        # Check cue and log files are present
        for x in ("cue", "log"):
            f = files_by_ext(self.files, x)
            if not f:
                print("No *.{} file found".format(x))
            elif len(f) > 1:
                print("Multiple *.{} files found".format(x))

        # Check if m3u files are present
        for x in ("m3u", "m3u8"):
            if files_by_ext(self.files, x):
                print("*.{} file detected - delete it".format(x))

    def _find_tracks(self):
        return [Track(self, os.path.join(self.directory, x))
                for x in self.files if has_ext(x, "flac")]


class Track(ValidatorBase):

    REQUIRED_TAGS = {"ARTIST", "TRACKNUMBER", "TITLE"}
    REPLAYGAIN_TAGS = {"REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_TRACK_PEAK"}

    def __init__(self, disc, path):
        self.disc = disc
        self.path = path
        self.name = os.path.basename(path)
        self.song = taglib.File(path)
        self.tags = self.song.tags

    @validator
    def validate(self):
        # Ensure the total path length is ok
        rel_path = os.path.relpath(self.path, start=self.disc.album.parent_dir)
        pathlen = len(rel_path)
        if pathlen > MAX_PATH_LENGTH:
            print("The path '{}' is too long ({} > {})".format(rel_path, pathlen, MAX_PATH_LENGTH))

        # TODO: Figure out the return code if the md5 doesn't exist vs is invalid
        if subprocess.call(["flac", "-ts", self.path]) != 0:
            # To fix no MD5: `flac --best -f <file>`
            print("Failed to verify FLAC file - it may be corrupt")

        # TODO: Make sure there's no embedded album art


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("albums", nargs="+", help="The album(s) to check")
    args = parser.parse_args()
    for album in args.albums:
        Album(album).validate()


if __name__ == "__main__":
    main()
