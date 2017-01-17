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

 - The extra info:
   - checks if a cue and log file are provided at the disc level
   - checks a cover image is provided at the album level
   - checks if an m3u file shoudl be deleted
   - [TODO] check a folder with additional art is provided at the album level

 - The folder/file names:
   - Check for invalid characters
   - Validates a specific naming scheme
     - album folder: [<ALBUMARTIST> - ]<ALBUM> (<YEAR>) \[<MEDIA>-FLAC[-<QUALITY>]\][ {<OTHER>}]
     - disc folder: (CD|Disc )<DISCNUMBER>
     - track name: <TRACKNUMBER> - [<ARTIST> - ]<TITLE>.flac
   - Validates the name against vorbis information
   - [TODO] don't require cue/log for non-CD SOURCE

 - vorbis information:
   - album, date, albumartist, and disctotal are at the album level
   - discnumber, tracktotal are at the disc level
   - artist, tracknumber, title are at the track level
   - checks for totaldiscs and totaltracks metadata
   - checks that disctotal is equal to the amount of discs
   - checks that tracktotal is equal to the number of tracks
   - checks for duplicate tags
   - checks the COMPILATION tag
   - Warns if album art is embedded
   - [TODO] warn if TRACKNUMBER is the "tracknum/totaltracks" style
   - [TODO] warn on extra whitespace in tags

 - replaygain information:
   - checks reference loudness, album gain, album peak are at the disc level
   - checks track gain and track peak are at the track level
"""

import argparse
import enum
import functools
import itertools
import os
import re
import shutil
import subprocess
import sys

import taglib

MAX_PATH_LENGTH = 180
COVER_FILENAME = "cover.jpg"
VARIOUS_ARTISTS = "Various Artists"
TAG_TRANSLATION = str.maketrans('<>:\/|"', "[]----'", "?*")
EXTERNALS = {x: bool(shutil.which(x)) for x in ("flac", "metaflac")}


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


def readable_regex(regex):
    """Make regular expressions more readable

    (probably not a good general solution but works for the name regexps)

    Note that the caron designates markup where it could be confused with the
    literal character. Messy but understandable.
    """
    COMB = "\N{COMBINING CARON}"
    c = lambda x, y: "".join((x[0], COMB, y, x[1], COMB))

    s = regex.pattern.rstrip("$").lstrip("^")
    # Convert named groups to just their names
    s = re.sub('\(\?P<(.*?)>.*?\)', '<\\1>', s)
    # Enclose non-capturing optional groups in []
    s = re.sub('\(\?:(.*?)\)\?', c('[]', '\\1'), s)
    # Remove the ?: from non-capturing non-optional groups
    s = re.sub('\(\?:(.*?)\)', c('()', '\\1'), s)
    # Ignore any extra ?+* (and preceeding characters unless +)
    s = re.sub('.[?*]|(.)\+', '\\1', s)
    # Remove escapes for {}()[]
    s = re.sub('\\\\([][(){}])', '\\1' , s)
    return s


quiet_call = functools.partial(subprocess.call, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)


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

    def _get_tag_and_check(self, tag_name):
        code, multiple, _ = self._check_all_same(tag_name)
        if code == Missing.NONE and not multiple:
            return self.get_valid_tag(tag_name), code, multiple
        return None, code, multiple

    def validate_all_same(self, tag):
        code, multiple, msgs = self._check_all_same(tag)

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

    def validate_name(self):
        if self.name is None:
            return

        if self.name != self.name.translate(TAG_TRANSLATION):
            print("Invalid characters detected in the {} name: '{}'".format(self.filetype, self.name))

        m = self.NAME_REGEX.match(self.name)
        if not m:
            print("Incorrect {} {} name - correct format is '{}'".format(self.level, self.filetype, readable_regex(self.NAME_REGEX)))
            return

        metadata = {k: v for k, v in m.groupdict().items() if v is not None}
        for x in self.REQUIRED_TAGS & metadata.keys():
            tag = self.get_valid_tag(x)
            if tag is None:
                print("Unable to validate {} against {} name (see above)".format(x, self.filetype))
                continue
            name = metadata[x]

            tag = tag.translate(TAG_TRANSLATION)
            if tag != name:
                print("Mismatch in tag {}: {}='{}', tag='{}'".format(x, self.filetype, name, tag))

        # Album-specific
        if isinstance(self, Album):
            # Warn about missing OTHERINFO
            if "OTHERINFO" not in metadata:
                print("No extra identifying information is included in the folder name")

            # Check optional albumartist
            albumartist = metadata.get("ALBUMARTIST", None)
            if albumartist == VARIOUS_ARTISTS:
                print("An artist of '{}' should not be included in the folder name".format(VARIOUS_ARTISTS))
                albumartist = None

            if albumartist is None and self.get_valid_tag("COMPILATION") != "1":
                print("No/various ALBUMARTIST specified in the folder name but not tagged as a compilation")

        # Track-specific
        elif isinstance(self, Track):
            # Check if the artist should be in the filename
            discartist, missing, multiple = self.disc._get_tag_and_check("ARTIST")
            if discartist is not None and "ARTIST" in metadata:
                print("ARTIST tags are all the same and therefore shouldn't be in the track name")
            elif multiple and "ARTIST" not in metadata:
                print("Multiple ARTIST tags - the track should include the ARTIST")

    def pre_validate(self):
        if self.name is None:
            print("Validating the only {}".format(self.level))
        else:
            print("Validating {}: {}".format(self.level, self.name))

        self.validate_metadata_structure()
        self.validate_replaygain()
        self.validate_number_metadata()
        self.validate_name()

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
    def filetype(self):
        if isinstance(self, Track):
            return "file"
        return "folder"

    @property
    def children(self):
        if isinstance(self, Album):
            return self.discs
        elif isinstance(self, Disc):
            return self.tracks
        return None


class Album(ValidatorBase):

    REQUIRED_TAGS = {"ALBUM", "DATE", "ALBUMARTIST", "DISCTOTAL", "MEDIA"}
    NAME_REGEX = re.compile("^(?:(?P<ALBUMARTIST>.*?) - )?(?P<ALBUM>.*) \((?P<DATE>.*)\) \[(?P<MEDIA>.+?) ?- ?FLAC(?: ?- ?(?P<QUALITY>[^\]]*))?\](?: \{(?P<OTHERINFO>.*)\})?$")

    def __init__(self, directory):
        self.directory = os.path.abspath(directory)

        if not os.path.isdir(self.directory):
            raise FileNotFoundError("Directory '{}' does not exist".format(self.directory))

        self.parent_dir = os.path.dirname(self.directory)
        self.name = os.path.basename(self.directory)
        self.discs = self._find_discs()

    def validate_compilation(self):
        """Validate the relationship between ARTIST, ALBUMARTIST and COMPILATION"""
        # Validate compilation tag
        compilation, c_missing, _ = self._get_tag_and_check("COMPILATION")
        if not (c_missing == Missing.ALL or (c_missing == Missing.NONE and compilation == "1")):
            print("Invalid COMPILATION tag: must all be set to '1' or unset")

        # Blank ALBUMARTIST, same ARTIST
        albumartist, aa_missing, _ = self._get_tag_and_check("ALBUMARTIST")
        artist, a_missing, multiple_artists = self._get_tag_and_check("ARTIST")
        if aa_missing == Missing.ALL and artist is not None:
            print("ALBUMARTIST tag should be set to '{}' (is unset but ARTIST tags are all the same)".format(artist))

        # same ARTISTS, different than ALBUMARTIST
        if None not in (artist, albumartist) and artist != albumartist:
            print("ALBUMARTIST is set to '{}' but all the ARTIST tags are '{}'".format(albumartist, artist))

        # Different ARTISTs, not a compilation
        if albumartist == VARIOUS_ARTISTS and compilation != "1":
            print ("ALBUMARTIST is set to '{}' but COMPILATION is not set".format(VARIOUS_ARTISTS))

        # Not a compilation, but different ARTISTS
        if compilation != "1" and multiple_artists:
            print("COMPILATION is not set but there are multiple different ARTISTs tags")

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

    REQUIRED_TAGS = {"DISCNUMBER", "TRACKTOTAL", "LABEL", "CATALOGNUMBER"}
    REPLAYGAIN_TAGS = {"REPLAYGAIN_REFERENCE_LOUDNESS", "REPLAYGAIN_ALBUM_GAIN",
                       "REPLAYGAIN_ALBUM_PEAK"}
    NAME_REGEX = re.compile("^(?:CD|Disc )(?P<DISCNUMBER>[^ ]*)$")

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
    NAME_REGEX = re.compile("^(?P<TRACKNUMBER>[^ ]*) - (?:(?P<ARTIST>.*) - )?(?P<TITLE>.*).flac$")

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

        # Don't allow various artists in the ARTIST tag
        if self.get_valid_tag("ARTIST") == VARIOUS_ARTISTS:
            print ("Invalid ARTIST: can't be '{}' (use ALBUMARTIST instead)".format(VARIOUS_ARTISTS))

        if EXTERNALS["flac"]:
            # Verify flac MD5 information
            if quiet_call(["flac", "--test", "--warnings-as-errors", self.path]) != 0:
                # To fix no MD5: `flac --best -f <file>`
                print("Failed to verify FLAC file - it may be corrupt or not have an MD5 set")

        if EXTERNALS["metaflac"]:
            # Make sure there's no embedded album art
            if quiet_call(["metaflac", "--export-picture-to=-", self.path]) == 0:
                print("Album art is embedded - remove it and provide a high-res '{}' instead.".format(COVER_FILENAME))


def main():

    if sys.version_info < (3, 3):
        print("check-flac requires Python 3.3+ to run")
        return 1

    # Warn for missing executables
    for k, v in EXTERNALS.items():
        if not v:
            print("WARNING: couldn't find the '{}' executable - some features will be unavailable".format(k))

    parser = argparse.ArgumentParser()
    parser.add_argument("albums", nargs="+", help="The album(s) to check")
    args = parser.parse_args()
    for album in args.albums:
        Album(album).validate()

    return 0

if __name__ == "__main__":
    sys.exit(main())
