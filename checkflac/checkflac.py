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
   - checks if a cue and log file are provided at the disc level (for CD source only)
   - checks a cover image is provided at the album level
   - checks if an m3u file shoudl be deleted
   - [TODO] check a folder with additional art is provided at the album level

 - The folder/file names:
   - Check for invalid characters
   - Validates a specific naming scheme
     - album folder: [<ALBUMARTIST> - ]<ALBUM> (<ORIGYEAR>) \[<MEDIA>-FLAC[-<QUALITY>]\][ {<OTHER>}]
     - disc folder: (CD|Disc )<DISCNUMBER>
     - track name: <TRACKNUMBER> - [<ARTIST> - ]<TITLE>.flac
   - Validates the name against vorbis information

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
   - Warns on sort tags (ALBUMSORT, TITLESORT, ARTISTSORT, etc)
   - [TODO] warn if TRACKNUMBER is the "tracknum/totaltracks" style
   - [TODO] warn on extra whitespace in tags

 - replaygain information:
   - checks reference loudness, album gain, album peak are at the disc level
   - checks track gain and track peak are at the track level
"""

import argparse
import enum
import functools
import os
import re
import shutil
import subprocess
import sys

import taglib

MAX_PATH_LENGTH = 180
COVER_FILENAME = "cover.jpg"
VARIOUS_ARTISTS = set(["various artists", "various", "va"])
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

def compare_names(tag, name, tagname=None):
    """Compare a tag against a filename and return if they're the same

    Common substitutions will be tried.
    Dates will only require the year to match (if more than a year is provided)
    """
    # Special comparison for dates (only check the year)
    if tagname in {"DATE", "ORIGDATE"}:
        return name == tag.split("-", 1)[0]

    if tag.translate(TAG_TRANSLATION) == name:
        return True

    # Allows "Album: Live in City" to match "Album - Live in City"
    if tag.replace(":" , " :").translate(TAG_TRANSLATION) == name:
        return True

    return False


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


def remove_optional_regex(pattern, name):
    """Removes an optional part of the regex by capture name

    Must be of the format '(?:[anything](?P<[name]>[anything])[anything])?'
    """
    return re.sub("\(\?:[^(]*\(\?P<{}>[^)]*\)[^)]*\)\?".format(name), "",
                  pattern)


quiet_call = functools.partial(subprocess.call, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)


class Missing(enum.Enum):
    NONE = 0
    SOME = 1
    ALL = 2


class Level(enum.Enum):
    album = "album"
    disc = "disc"
    track = "track"

    def __str__(self):
        return str(self.value)

    @staticmethod
    def values():
        return [x.value for x in Level]

    @staticmethod
    def classify(obj):
        if isinstance(obj, Album):
            return Level.album
        elif isinstance(obj, Disc):
            return Level.disc
        elif isinstance(obj, Track):
            return Level.track
        raise ValueError("Object '{!r}' is not a Level".format(obj))


class ValidatorBase(object):

    REQUIRED_TAGS = set()
    REPLAYGAIN_TAGS = set()

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
        if code is Missing.NONE and not multiple:
            return self.get_valid_tag(tag_name), code, multiple
        return None, code, multiple

    def validate_all_same(self, tag):
        code, multiple, msgs = self._check_all_same(tag)

        # This is expected for compilations - catch this later if it's not
        if tag == "ALBUMARTIST" and code is Missing.ALL:
            return

        # This is expected for the original release
        if tag == "ORIGDATE" and code is Missing.ALL:
            return

        if code is not Missing.NONE or multiple:
            if self.level is Level.track:
                # Track can't have multiple values
                print("Problem with tag {}: missing".format(tag))
            else:
                print("Problem with tag {}: {}".format(tag, ", ".join(msgs)))

    def validate_number_metadata(self):
        # Check for invalid [type]TOTAL metadata
        if self.level is Level.album:
            tag = "DISC"
        elif self.level is Level.disc:
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

        if not compare_names(self.name, self.name):
            print("Invalid characters detected in the {} name: '{}'".format(self.filetype, self.name))

        m = self.NAME_REGEX.match(self.name)
        if not m:
            print("Incorrect {} {} name - correct format is '{}'".format(self.level, self.filetype, readable_regex(self.NAME_REGEX)))
            return

        metadata = {k: v for k, v in m.groupdict().items() if v is not None}
        for tagname in self.REQUIRED_TAGS & metadata.keys():
            tag = self.get_valid_tag(tagname)
            name = metadata[tagname]

            # Special case handling for album ORIGDATE/DATE
            # assume DATE is the ORIGDATE if no ORIGDATE is provided
            if tag is None and tagname == "ORIGDATE":
                tagname = "DATE"
                tag = self.get_valid_tag(tagname)

            if tag is None:
                print("Unable to validate {} against {} name (see above)".format(tagname, self.filetype))
                continue

            if not compare_names(tag, name, tagname):
                print("Mismatch in tag {}: {}='{}', tag='{}'".format(tagname, self.filetype, name, tag))

        # Album-specific
        if self.level is Level.album:
            # Warn about missing OTHERINFO
            if "OTHERINFO" not in metadata:
                print("No extra identifying information is included in the folder name")

            # Don't require cue/log files for non-cd rips (assume CD)
            if metadata.get("MEDIA", "CD") != "CD":
                self.config.no_cue_log = True

            # Check optional albumartist
            albumartist = metadata.get("ALBUMARTIST", None)
            albumartist_tag = self.get_valid_tag("ALBUMARTIST")

            if albumartist_tag is not None and albumartist is None:
                print("No ALBUMARTIST found in the folder name but found in the tags")

            if albumartist_tag is None and albumartist is not None:
                print("ALBUMARTIST is in the folder name but is not in the tags")

            if albumartist and albumartist.lower() in VARIOUS_ARTISTS:
                print("An artist of '{}' should not be included in the folder name".format(albumartist))
                albumartist = None

            if albumartist is None and self.get_valid_tag("COMPILATION") != "1":
                print("No/various ALBUMARTIST specified in the folder name but not tagged as a compilation")

        # Track-specific
        elif self.level is Level.track:
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
            print("Validating {}".format(self))

        self.validate_metadata_structure()
        if not self.config.no_replaygain:
            self.validate_replaygain()
        self.validate_number_metadata()
        self.validate_name()

    @validator
    def validate():
        pass

    def post_validate(self):
        if self.level is self.config.checklevel:
            return
        for x in self.children:
            x.validate()

    def get_tag(self, tag_name, placeholder=False):
        if self.level is Level.track:
            # Down at the track level, return the tag
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
        else:
            return list(t for c in self.children for t in c.get_tag(tag_name, placeholder))

    def get_valid_tag(self, tag_name):
        """Get a tag's valid if all children have the same one (otherwise None)"""
        tags = set(self.get_tag(tag_name))
        if len(tags) == 1:
            return next(iter(tags))
        return None

    @property
    def level(self):
        return Level.classify(self)

    @property
    def filetype(self):
        if self.level is Level.track:
            return "file"
        return "folder"

    @property
    def config(self):
        if self.level is Level.album:
            return self._config
        elif self.level is Level.disc:
            return self.album._config
        else:
            return self.disc.album._config

    @property
    def children(self):
        if self.level is Level.album:
            return self.discs
        elif self.level is Level.disc:
            return self.tracks
        return None

    def __repr__(self):
        return "<{} '{}'>".format(self.level, self.name)


class Album(ValidatorBase):

    REQUIRED_TAGS = {"ALBUM", "DATE", "ORIGDATE", "ALBUMARTIST", "DISCTOTAL", "MEDIA"}
    _NAME_PATTERN = "^(?:(?P<ALBUMARTIST>.*?) - )?(?P<ALBUM>.*) \((?P<ORIGDATE>.*)\) \[(?P<MEDIA>.+?) ?- ?FLAC(?: ?- ?(?P<QUALITY>[^\]]*))?\](?: \{(?P<OTHERINFO>.*)\})?$"

    def __init__(self, directory, config):
        super().__init__()
        # Keep a copy of the config - our changes shouldn't affect other Albums
        self._config = argparse.Namespace(**vars(config))
        self.directory = os.path.abspath(directory)

        if not os.path.isdir(self.directory):
            raise FileNotFoundError("Directory '{}' does not exist".format(self.directory))

        self.parent_dir = os.path.dirname(self.directory)
        self.name = os.path.basename(self.directory)
        self.discs = self._find_discs()

        if self.config.no_albumartist:
            self.NAME_REGEX = re.compile(remove_optional_regex(self._NAME_PATTERN, "ALBUMARTIST"))
        else:
            self.NAME_REGEX = re.compile(self._NAME_PATTERN)

    def validate_compilation(self):
        """Validate the relationship between ARTIST, ALBUMARTIST and COMPILATION"""
        # Validate compilation tag
        compilation, c_missing, _ = self._get_tag_and_check("COMPILATION")
        if not (c_missing is Missing.ALL or (c_missing is Missing.NONE and compilation == "1")):
            print("Invalid COMPILATION tag: must all be set to '1' or unset")

        # Blank ALBUMARTIST, same ARTIST
        albumartist, aa_missing, _ = self._get_tag_and_check("ALBUMARTIST")
        artist, a_missing, multiple_artists = self._get_tag_and_check("ARTIST")
        if aa_missing is Missing.ALL and artist is not None:
            print("ALBUMARTIST tag should be set to '{}' (is unset but ARTIST tags are all the same)".format(artist))

        # same ARTISTS, different than ALBUMARTIST
        if None not in (artist, albumartist) and artist != albumartist:
            print("ALBUMARTIST is set to '{}' but all the ARTIST tags are '{}'".format(albumartist, artist))

        # Different ARTISTs, not a compilation
        if (albumartist and albumartist.lower() in VARIOUS_ARTISTS) and compilation != "1":
            print ("ALBUMARTIST is set to '{}' but COMPILATION is not set".format(albumartist))

        # Not a compilation, but different ARTISTS
        if compilation != "1" and multiple_artists:
            print("COMPILATION is not set but there are multiple different ARTISTs tags")

    def validate_albumartist(self):
        albumartist = self.get_valid_tag("ALBUMARTIST")
        if albumartist and albumartist.lower() in VARIOUS_ARTISTS:
            print("The ALBUMARTST tag is '{}' - for albums without a main "
                  "artist it should be deleted instead".format(albumartist))

    @validator
    def validate(self):
        self.validate_compilation()
        self.validate_albumartist()

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
        super().__init__()
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
        if not self.album.config.no_cue_log:
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
    _NAME_PATTERN = "^(?P<TRACKNUMBER>[^ ]*) - (?:(?P<ARTIST>.*) - )?(?P<TITLE>.*).flac$"

    def __init__(self, disc, path):
        super().__init__()
        self.disc = disc
        self.path = path
        self.name = os.path.basename(path)
        self.song = taglib.File(path)
        self.tags = self.song.tags

        if self.config.no_trackartist:
            self.NAME_REGEX = re.compile(remove_optional_regex(self._NAME_PATTERN, "ARTIST"))
        else:
            self.NAME_REGEX = re.compile(self._NAME_PATTERN)

    def check_sort_tags(self):
        """Make sure no *SORT tags are set on the track"""
        for x in self.tags:
            if x.endswith("SORT"):
                print("Sorting tag '{}' found - should be removed".format(x))

    @validator
    def validate(self):
        # Ensure the total path length is ok
        rel_path = os.path.relpath(self.path, start=self.disc.album.parent_dir)
        pathlen = len(rel_path)
        if pathlen > MAX_PATH_LENGTH:
            print("The path '{}' is too long ({} > {})".format(rel_path, pathlen, MAX_PATH_LENGTH))

        self.check_sort_tags()

        # Don't allow various artists in the ARTIST tag
        artist = self.get_valid_tag("ARTIST")
        if artist and artist.lower() in VARIOUS_ARTISTS:
            print ("Invalid ARTIST: can't be '{}' (use ALBUMARTIST instead)".format(artist))

        if not self.config.no_flactest and EXTERNALS["flac"]:
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
    parser.add_argument("--checklevel", action="store", type=str, choices=tuple(Level.values()), default=str(Level.track), help="The level to check down to (default: %(default)s)")
    parser.add_argument("--no-replaygain", action="store_true", help="Don't check for any replaygain tags")
    parser.add_argument("--no-flactest", action="store_true", help="Don't test flac files for corruption/errors (can be slow)")
    parser.add_argument("--no-albumartist", action="store_true", help="Assume the album artist is NOT in the foldername (default is to detect this automatically, only enable if you have issues)")
    parser.add_argument("--no-trackartist", action="store_true", help="Assume the artist is NOT in track filenames (default is to detect this automatically, only enable if you have issues)")
    parser.add_argument("--no-cue-log", action="store_true", help="Don't look for any *.cue or *.log files (this is the default for non-CD media)")

    config = parser.parse_args()
    albums = config.albums

    # Massage the config a bit
    delattr(config, "albums")
    config.checklevel = Level(config.checklevel)

    for album in albums:
        Album(album, config).validate()

    return 0

if __name__ == "__main__":
    sys.exit(main())
