#!/usr/bin/env python3

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
   - [TODO] check a cover image is provided at the album level
   - [TODO] check a folder with additional art is provided at the album level

 - The folders:
   - [TODO] validate a naming scheme

 - vorbis information:
   - album, date, albumartist, and disctotal are at the album level
   - discnumber, tracktotal are at the disc level
   - artist, tracknumber, title are at the track level
   - checks for totaldiscs and totaltraks metadata
   - checks that disctotal is equal to the amount of discs
   - checks that tracktotal is equal to the number of tracks
   - [TODO] warn if TRACKNUMBER is the "tracknum/totaltracks" style
   - [TODO] warn if album art is embedded
   - [TODO] warn about duplicate tags

 - replaygain information:
   - reference loudness, album gain, album peak are at the disc level
   - track gain and track peak are at the track level
"""

import argparse
import itertools
import subprocess
import os

import taglib

MAX_PATH_LENGTH = 180
REQUIRED_TAGS_ALBUM = {"ALBUM", "DATE", "ALBUMARTIST", "DISCTOTAL"}
REQUIRED_TAGS_DISC = {"DISCNUMBER", "TRACKTOTAL"}
REQUIRED_TAGS_TRACK = {"ARTIST", "TRACKNUMBER", "TITLE"}
REPLAYGAIN_TAGS_DISC = {"REPLAYGAIN_REFERENCE_LOUDNESS", "REPLAYGAIN_ALBUM_GAIN",
                        "REPLAYGAIN_ALBUM_PEAK"}
REPLAYGAIN_TAGS_TRACK = {"REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_TRACK_PEAK"}


def all_same(lst):
    """More than 1, all the same and not None"""
    if not lst:
        return False
    f = lst[0]
    if f is None:
        return False
    return all(f == x for x in lst)


def has_ext(path, ext):
    return path.rsplit(".", 1)[-1].lower() == ext.lower()


def single_file(files, ext):
    temp = [x for x in files if has_ext(x, ext)]
    if len(temp) > 1:
        print("More than 1 '*.{}' file found".format(ext))
        return None
    return temp[0] if temp else None


class Album(object):

    def __init__(self, album_dir):
        # Remove trailing slashes (can cause problems with os.path.dirname)
        album_dir = album_dir.rstrip("/")

        if not os.path.isdir(album_dir):
            raise FileNotFoundError("Directory '{}' does not exist".format(album_dir))

        album_dir = os.path.abspath(album_dir)

        self.directory = album_dir
        self.parent_dir = os.path.dirname(album_dir)
        self.name = os.path.basename(album_dir)
        self.discs = self._find_discs()

    def validate(self):
        print("Validating album: {}".format(self.name))

        for tag in REQUIRED_TAGS_ALBUM:
            if not all_same(self.get_tags(tag)):
                print("Problem with album-level tag {} (missing/not all the same)".format(tag))

        # Check TOTALDISCS doesn't exist
        if self.get_tags("TOTALDISCS"):
            if self.get_tags("DISCTOTAL"):
                print ("TOTALDISCS tags detected, delete them (DISCTOTAL tag already exists)")
            else:
                print ("TOTALDISCS tags detected, convert them to DISCTOTAL")

        # Check DISCTOTAL = number of discs
        temp = self.get_tags("DISCTOTAL")
        if temp:
            try:
                total_discs = int(temp[0])
            except ValueError:
                print("Problem with DISCTOTAL tag (non-numeric)")
            else:
                if total_discs != len(self.discs):
                    print("Problem with DISCTOTAL tag (incorrect number of discs)")

        # Validate individual discs
        for d in self.discs:
            d.validate()

    def _find_discs(self):
        ret = []
        for dirpath, dirs, files in os.walk(self.directory):
            if not any(x for x in files if has_ext(x, "flac")):
                continue

            ret.append(Disc(self, dirpath, files))
        return ret

    def get_tags(self, tag_name):
        return list(itertools.chain.from_iterable(d.get_tags(tag_name)
                                                  for d in self.discs))


class Disc(object):

    def __init__(self, album, directory, files):
        self.album = album
        self.directory = directory
        self.tracks = self._find_tracks(files)
        self.log = single_file(files, "log")
        self.cue = single_file(files, "cue")

        if directory != self.album.directory:
            self.name = os.path.basename(directory)
        else:
            self.name = None

    def validate(self):
        if self.name is not None:
            print("Validating disc: {}".format(self.name))
        else:
            print("Validating the only disc")

        if not self.log:
            print("No log file found!")
        if not self.cue:
            print("No cue file found!")

        for tag in REQUIRED_TAGS_DISC:
            if not all_same(self.get_tags(tag)):
                print("Problem with disc-level tag {} (missing/not all the same)".format(tag))

        for tag in REPLAYGAIN_TAGS_DISC:
            if not all_same(self.get_tags(tag)):
                # To fix replaygain: `metaflac --add-replay-gain <all files from disc>`
                print("Problem with disc-level replaygain tag {} (missing/not all the same)".format(tag))

        # Check TOTALTRACKS doesn't exist
        if self.get_tags("TOTALTRACKS"):
            if self.get_tags("TRACKTOTAL"):
                print ("TOTALTRACKS tags detected, delete them (TRACKTOTAL tag already exists)")
            else:
                print ("TOTALTRACKS tags detected, convert them to TRACKTOTAL")

        # Check number of tracks = TRACKTOTAL
        tracknumbers = self.get_tags("TRACKNUMBER")
        temp_tracks = self.get_tags("TRACKTOTAL")
        if tracknumbers and temp_tracks:
            num_tracks = len(tracknumbers)
            total_tracks = int(temp_tracks[0])
            if num_tracks != total_tracks:
                print("Different number of TRACKNUMBERs than TRACKTOTAL")

            # Check sort order (TODO: padding + possible letters)
            try:
                tracknumbers = [int(x) for x in tracknumbers]
            except TypeError:
                print("WARNING: Not checking sort order (tracknumbers are non-numeric in metadata)")
            else:
                if sorted(tracknumbers) != tracknumbers:
                    print("Files do not sort properly according to the tracknumber metadata")

        # Validate individual tracks
        for t in self.tracks:
            t.validate()

    def _find_tracks(self, files):
        # Sort the files by name to later validate they sort correctly by tracknumber too
        return [Track(self, os.path.join(self.directory, x))
                for x in sorted(files) if has_ext(x, "flac")]

    def get_tags(self, tag_name):
        return list(itertools.chain.from_iterable(t.get_tags(tag_name)
                                                  for t in self.tracks))


class Track(object):

    def __init__(self, disc, path):
        self.disc = disc
        self.path = path
        self.name = os.path.basename(path)
        self.song = taglib.File(path)
        self.tags = self.song.tags

    def validate(self):
        print("Validating track: {}".format(self.name))

        for tag in REQUIRED_TAGS_TRACK:
            if not self.get_tags(tag):
                print("Problem with track-level tag {} (missing/blank)".format(tag))

        for tag in REPLAYGAIN_TAGS_TRACK:
            if not self.get_tags(tag):
                print("Problem with track-level replaygain tag {} (missing/blank)".format(tag))

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

    def get_tags(self, tag_name):
        if tag_name in self.tags:
            tag = self.tags[tag_name]

            # Check for duplicate tags
            num_tags = len(tag)
            if num_tags > 1:
                print("Multiple '{}' tags ({})".format(tag_name, num_tags))
            return [tag[0]]
        return list()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("albums", nargs="+", help="The album(s) to check")
    args = parser.parse_args()
    for album in args.albums:
        Album(album).validate()


if __name__ == "__main__":
    main()
