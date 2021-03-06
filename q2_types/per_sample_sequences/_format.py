# ----------------------------------------------------------------------------
# Copyright (c) 2016-2017, QIIME 2 development team.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file LICENSE, distributed with this software.
# ----------------------------------------------------------------------------

import itertools

import skbio
import skbio.io
import yaml
import qiime2.plugin.model as model

from ..plugin_setup import plugin


class _FastqManifestBase(model.TextFileFormat):
    """
    Base class for mapping of sample identifiers to filepaths and read
    direction.

    """
    EXPECTED_HEADER = None

    def sniff(self):
        with self.open() as fh:
            data_lines = 0
            header = None
            while data_lines < 10:
                line = fh.readline()

                if line == '':
                    # EOF
                    break
                elif line.lstrip(' ') == '\n':
                    # Blank line
                    continue
                elif line.startswith('#'):
                    # Comment line
                    continue

                cells = line.rstrip('\n').split(',')
                if header is None:
                    if cells != self.EXPECTED_HEADER:
                        return False
                    header = cells
                else:
                    if len(cells) != len(header):
                        return False
                    data_lines += 1

            return header is not None and data_lines > 0


class FastqManifestFormat(_FastqManifestBase):
    """
    Mapping of sample identifiers to relative filepaths and read direction.

    """
    EXPECTED_HEADER = ['sample-id', 'filename', 'direction']


class FastqAbsolutePathManifestFormat(_FastqManifestBase):
    """
    Mapping of sample identifiers to absolute filepaths and read direction.

    """
    EXPECTED_HEADER = ['sample-id', 'absolute-filepath', 'direction']


class SingleEndFastqManifestPhred33(FastqAbsolutePathManifestFormat):
    pass


class SingleEndFastqManifestPhred64(FastqAbsolutePathManifestFormat):
    pass


class PairedEndFastqManifestPhred33(FastqAbsolutePathManifestFormat):
    pass


class PairedEndFastqManifestPhred64(FastqAbsolutePathManifestFormat):
    pass


class YamlFormat(model.TextFileFormat):
    """
    Arbitrary yaml-formatted file.

    """
    def sniff(self):
        with self.open() as fh:
            try:
                yaml.safe_load(fh)
            except yaml.YAMLError:
                return False
        return True


class FastqGzFormat(model.BinaryFileFormat):
    """
    A gzipped fastq file.

    """
    def sniff(self):
        with self.open() as fh:
            if fh.read(2)[:2] != b'\x1f\x8b':
                return False

        filepath = str(self)
        sniffer = skbio.io.io_registry.get_sniffer('fastq')
        if sniffer(str(self))[0]:
            try:
                generator = skbio.io.read(filepath, constructor=skbio.DNA,
                                          phred_offset=33, format='fastq',
                                          verify=False)
                for seq, _ in zip(generator, range(15)):
                    pass
                return True
            # ValueError raised by skbio if there are invalid DNA chars.
            except ValueError:
                pass
        return False


class CasavaOneEightSingleLanePerSampleDirFmt(model.DirectoryFormat):
    sequences = model.FileCollection(
        r'.+_.+_L[0-9][0-9][0-9]_R[12]_001\.fastq\.gz',
        format=FastqGzFormat)

    @sequences.set_path_maker
    def sequences_path_maker(self, sample_id, barcode_id, lane_number,
                             read_number):
        return '%s_%s_L%03d_R%d_001.fastq.gz' % (sample_id, barcode_id,
                                                 lane_number, read_number)


class _SingleLanePerSampleFastqDirFmt(CasavaOneEightSingleLanePerSampleDirFmt):
    manifest = model.File('MANIFEST', format=FastqManifestFormat)
    metadata = model.File('metadata.yml', format=YamlFormat)


class SingleLanePerSampleSingleEndFastqDirFmt(_SingleLanePerSampleFastqDirFmt):
    pass


class SingleLanePerSamplePairedEndFastqDirFmt(_SingleLanePerSampleFastqDirFmt):
    # There is no difference between this and
    # SingleLanePerSampleSingleEndFastqDirFmt (canonically pronounced,
    # SLPSSEFDF) until we have validation.
    pass


class CasavaOneEightLanelessPerSampleDirFmt(model.DirectoryFormat):
    sequences = model.FileCollection(r'.+_.+_R[12]_001\.fastq\.gz',
                                     format=FastqGzFormat)

    @sequences.set_path_maker
    def sequences_path_maker(self, sample_id, barcode_id, read_number):
        return '%s_%s_R%d_001.fastq.gz' % (sample_id, barcode_id, read_number)


class QIIME1DemuxFormat(model.TextFileFormat):
    """QIIME 1 demultiplexed FASTA format.

    The QIIME 1 demultiplexed FASTA format is the default output format of
    ``split_libraries.py`` and ``split_libraries_fastq.py``. The file output by
    QIIME 1 is named ``seqs.fna``; this filename is sometimes associated with
    the file format itself due to its widespread usage in QIIME 1.

    The format is documented here:
    http://qiime.org/documentation/file_formats.html#demultiplexed-sequences

    Format details:

    - FASTA file with exactly two lines per record: header and sequence. Each
      sequence must span exactly one line and cannot be split across multiple
      lines.

    - The ID in each header must follow the format ``<sample-id>_<seq-id>``.
      ``<sample-id>`` is the identifier of the sample the sequence belongs to,
      and ``<seq-id>`` is an identifier for the sequence *within* its sample.
      In QIIME 1, ``<seq-id>`` is typically an incrementing integer starting
      from zero, but any non-empty value can be used here, as long as the
      header IDs remain unique throughout the file. Note: ``<sample-id>`` may
      contain sample IDs that contain underscores; the rightmost underscore
      will used to delimit sample and sequence IDs.

    - Descriptions in headers are permitted and ignored.

    - Header IDs must be unique within the file.

    - Each sequence must be DNA and cannot be empty.

    """

    def sniff(self):
        with self.open() as filehandle:
            try:
                self._validate(filehandle, num_records=30)
            except Exception:
                return False
            else:
                return True

    # The code is structured such that `_validate` can be used to validate as
    # much of the file as desired. Users may be able to control levels of
    # validation in the future, and we'll also have the ability to describe
    # *why* a file is invalid. Sniffers can only offer a boolean response
    # currently, but the below `Exceptions` could include real error messages
    # in the future. For now, the `Exceptions` are only used to give a boolean
    # response to the sniffer.
    def _validate(self, filehandle, *, num_records):
        ids = set()
        for (header, seq), _ in zip(itertools.zip_longest(*[filehandle] * 2),
                                    range(num_records)):
            if header is None or seq is None:
                # Not exactly two lines per record.
                raise Exception()

            header = header.rstrip('\n')
            seq = seq.rstrip('\n')

            id = self._parse_id(header)
            if id in ids:
                # Duplicate header ID.
                raise Exception()

            self._validate_id(id)
            self._validate_seq(seq)

            ids.add(id)

        if not ids:
            # File was empty.
            raise Exception()

    def _parse_id(self, header):
        if not header.startswith('>'):
            raise Exception()
        header = header[1:]

        id = ''
        if header and not header[0].isspace():
            id = header.split(maxsplit=1)[0]
        return id

    def _validate_id(self, id):
        pieces = id.rsplit('_', maxsplit=1)
        if len(pieces) != 2 or not all(pieces):
            raise Exception()

    def _validate_seq(self, seq):
        if seq:
            # Will raise a `ValueError` on invalid DNA characters.
            skbio.DNA(seq, validate=True)
        else:
            # Empty sequence.
            raise Exception()


QIIME1DemuxDirFmt = model.SingleFileDirectoryFormat(
    'QIIME1DemuxDirFmt', 'seqs.fna', QIIME1DemuxFormat)


plugin.register_formats(
    FastqManifestFormat, YamlFormat, FastqGzFormat,
    CasavaOneEightSingleLanePerSampleDirFmt,
    CasavaOneEightLanelessPerSampleDirFmt,
    _SingleLanePerSampleFastqDirFmt, SingleLanePerSampleSingleEndFastqDirFmt,
    SingleLanePerSamplePairedEndFastqDirFmt, SingleEndFastqManifestPhred33,
    SingleEndFastqManifestPhred64, PairedEndFastqManifestPhred33,
    PairedEndFastqManifestPhred64, QIIME1DemuxFormat, QIIME1DemuxDirFmt
)
