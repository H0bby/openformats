from copy import deepcopy
from lxml import etree


class Transcriber(object):
    r"""
    This class helps with creating a template from an imported file or compile
    an output file from a template.

    **Main functionality**

    This class will help with both creating a template from an imported file
    and with compiling a file from a template. It provides functions for
    copying text. It depends on 3 things, the source content (self.source), the
    target content (self.destination) which initially will contain an empty
    string and a pointer (self.ptr) which will indicate which parts of 'source'
    have already been copied to 'destination' (and will be initialized to 0).

    Transcriber detects and remembers the newline type (DOS, ``'\r\n'`` or UNIX
    ``'\n'``) of 'source'. It then converts 'source' to UNIX-like newlines and
    works on this. When returning the destination, the initial newline type
    will be used. Because 'source' is being potentially edited, it's a good
    idea to save Transcriber's source back on top of the original one:

        >>> def parse(self, source):
        ...     self.transcriber = Transcriber(source)
        ...     self.source = self.transcriber.source
        ...     # ...

    The main methods provided are demonstrated below::

        >>> transcriber = Transcriber(source)

        source:      <string name="foo">hello world</string>
        ptr:         ^ (0)
        destination: []

        >>> transcriber.copy_until(source.index('>') + 1)

        source:      <string name="foo">hello world</string>
        ptr:                            ^
        destination: ['<string name="foo">']

        >>> transcriber.add("aee8cc2abd5abd5a87cd784be_tr")

        source:      <string name="foo">hello world</string>
        ptr:                            ^
        destination: ['<string name="foo">', 'aee8cc2abd5abd5a87cd784be_tr']

        >>> transcriber.skip(len("hello world"))

        source:      <string name="foo">hello world</string>
        ptr:                                       ^
        destination: ['<string name="foo">', 'aee8cc2abd5abd5a87cd784be_tr']

        >>> transcriber.copy_until(source.index("</string>") +
        ...                        len("</string>"))

        source:      <string name="foo">hello world</string>
        ptr:                                                ^
        destination: ['<string name="foo">', 'aee8cc2abd5abd5a87cd784be_tr',
        '</string>']

        >>> print transcriber.get_destination()

        <string name="foo">aee8cc2abd5abd5a87cd784be_tr</string>
    """

    class SectionStart:
        pass

    class SectionEnd:
        pass

    def __init__(self, source):
        self.source = source
        self.destination = []
        self.ptr = 0

        self.newline_count = 0

        # Handle newlines
        self.newline_type = "UNIX"
        if '\r\n' in self.source:
            self.newline_type = "DOS"
            self.source = self.source.replace('\r\n', '\n')

    def copy(self, offset):
        chunk = self.source[self.ptr:self.ptr + offset]
        self.destination.append(chunk)
        self.ptr += offset

        self.newline_count += chunk.count('\n')

    def copy_until(self, end):
        chunk = self.source[self.ptr:end]
        self.destination.append(chunk)
        self.ptr = end

        self.newline_count += chunk.count('\n')

    def add(self, text):
        self.destination.append(text)

    def skip(self, offset):
        chunk = self.source[self.ptr:self.ptr + offset]
        self.newline_count += chunk.count('\n')

        self.ptr += offset

    def skip_until(self, end):
        chunk = self.source[self.ptr:end]
        self.newline_count += chunk.count('\n')

        self.ptr = end

    def mark_section_start(self):
        self.destination.append(self.SectionStart)

    def mark_section_end(self):
        self.destination.append(self.SectionEnd)

    def remove_section(self, place=0):
        """
        You can mark sections in the target file and optionally remove them.
        Insert the section-start and section-end bookmarks wherever you want to
        mark a section. Then you can remove a section with `remove_section()`.
        For example::

            >>> transcriber = Transcriber(source)

            source:      <keep><remove>
            ptr:         ^ (0)
            destination: []

            >>> start = 0

            >>> transcriber.mark_section_start()
            >>> transcriber.copy_until(start + 1)  # copy until first '<'
            >>> string = source[start + 1:source.index('>', start)]
            >>> transcriber.add("asdf")  # add the hash
            >>> transcriber.skip(len(string))
            >>> transcriber.copy_until(source.index('>', start) + 1)
            >>> transcriber.mark_section_end()

            source:      <keep><remove>
            ptr:               ^
            destination: [SectionStart, '<', 'asdf', '>', SectionEnd]

            >>> if string == "remove":
            ...     transcriber.remove_section()

            (nothing happens)

            >>> start = source.index('>') + 1

            >>> # Same deal as before, mostly
            >>> transcriber.mark_section_start()
            >>> transcriber.copy_until(start + 1)  # copy until second '<'
            >>> string = source[start + 1:source.index('>', start)]
            >>> transcriber.add("fdsa")  # add the hash
            >>> transcriber.skip(len(string))
            >>> transcriber.copy_until(source.index('>', start) + 1)
            >>> transcriber.mark_section_end()

            source:      <keep><remove>
            ptr:                       ^
            destination: [SectionStart, '<', 'asdf', '>', SectionEnd,
                          SectionStart, '<', 'fdsa', '>', SectionEnd]

            >>> if string == "remove":
            ...     transcriber.remove_section()

            source:      <keep><remove>
            ptr:                       ^
            destination: [SectionStart,  '<', 'asdf', '>',  SectionEnd,
                          None        , None, None  , None, None      ]

            (The last section was replaced with Nones)

            Now, when you try to get the result with `get_destination()`, the
            Nones, SectionStarts and SectionEnds will be ommited:

            >>> transcriber.get_destination()

            <asdf>
        """
        section_start_position = self._find_last_section_start(place)
        try:
            section_end_position = self.destination.index(
                self.SectionEnd, section_start_position
            )
        except ValueError:
            section_end_position = len(self.destination) - 1
        for i in range(section_start_position, section_end_position + 1):
            self.destination[i] = None

    def _find_last_section_start(self, place=0):
        count = place
        for i, segment in enumerate(self.destination[::-1], start=1):
            if segment == self.SectionStart:
                if count == 0:
                    return len(self.destination) - i
                else:
                    count -= 1

    @property
    def line_number(self):
        r"""
        The transcriber remembers how many newlines it has went over on the
        source, both when copying and skipping content. This allows you to
        pinpoint the line-number a parse-error has occured. For example::

            source:
                first line
                second line
                third line with error
                fourth line

            >>> transcriber = Transcriber(source)
            >>> for line in source.split("\n"):
            >>>     if "error" not in line:
            >>>         # include the newline too
            >>>         transcriber.copy(len(line) + 1)
            >>>     else:
            >>>         raise ParseError(
            >>>             "Error on line {line_no}: '{line}'".format(
            >>>                 line_no=transcriber.line_number,
            >>>                 line=line
            >>>             )
            >>>         )

            This will raise a::

            >>> ParseError("Error on line 3: 'third line with error'")
        """
        return self.newline_count + 1

    def get_destination(self, enforce_newline_type=None):
        return "".join([self.edit_newlines(chunk, enforce_newline_type)
                        for chunk in self.destination
                        if chunk not in (self.SectionStart, self.SectionEnd,
                                         None)])

    def edit_newlines(self, chunk, enforce_newline_type=None):
        r"""
        This is the part that renders the newlines to their correct type when
        returning the final result. You have the option to enforce the newline
        type if you want to.

            >>> source = "hello\r\nworld"
            >>> t = Transcriber(source)
            >>> t.source

            >>> "hello\nworld"

            >>> source = trascriber.source
            >>> # Work as if source was UNIX-type
            >>> t.copy_until(source.index('\n') + 1)  # include the '\n'
            >>> t.add("fellas")
            >>> t.get_destination()

            >>> "hello\r\nfellas"  # <- it remembered newline type from source

            >>> t.get_destination(enforce_newline_type="UNIX")

            >>> "hello\nfellas"
        """

        if ((enforce_newline_type is None and self.newline_type == "DOS") or
                enforce_newline_type == "DOS"):
            return chunk.replace('\n', '\r\n')
        else:
            return chunk


class LxmlTranscriber(object):
    """ Sample usage:

        source:
            <list>
                <line action="replace_with_hello_world">First line</line>
                <line action="drop_this">Second line</line>
                <line action="reverse_this">Third line</line>
                <line action="empty_this">Fourth line</line>
            </list>

        >>> transcriber = LxmlTranscriber(source)
        >>> for line in transcriber:
        ...     if line.attrib['action'] == "replace_with_hello_world":
        ...         line.replace_inner("hello world")
        ...     elif line.attrib['action'] == "drop_this":
        ...         line.drop()
        ...     elif line.attrib['action'] == "reverse_this":
        ...         text = line.extract_inner()
        ...         line.replace_inner(text[::-1])
        ...     elif line.attrib['action'] == "empty_this":
        ...         line.replace_inner("")
        >>> print transcriber.get_destincation()

        output:
            <list>
                <line action="replace_with_hello_world">hello world</line>
                <line action="reverse_this">enil drihT</line>
                <line action="empty_this"/>
            </list>
    """

    def __init__(self, source, encoding="UTF-8"):
        self.source = source
        if isinstance(self.source, unicode):
            self.source = etree.fromstring(self.source.encode(encoding))
        if isinstance(self.source, str):
            self.source = etree.fromstring(self.source)
        self.destination = deepcopy(self.source)
        self.dropped = False

    # Modify `etree.Element`'s iteration
    def __iter__(self):
        for element in self.destination:
            subtranscriber = self.__class__(element)
            yield subtranscriber
            if subtranscriber.dropped:
                self.destination.remove(element)
            else:
                self.destination.replace(element, subtranscriber.destination)

    # Transcriber utils
    def drop(self):
        self.dropped = True

    def extract_inner(self):
        string = etree.tostring(self.source)
        start = string.index('>') + 1
        end = len(string) - string[::-1].index('<') - 1
        return string[start:end]

    def replace_inner(self, text):
        self.destination.text = ""
        for item in self.destination:
            self.destination.remove(item)

        text_xml = etree.fromstring("<a>{}</a>".format(text))
        self.destination.text = text_xml.text
        for item in text_xml:
            self.destination.append(item)

    def get_destination(self):
        return etree.tostring(self.destination)

    # etree.Element wrappers
    @property
    def tag(self):
        return self.destination.tag

    @property
    def attrib(self):
        return self.destination.attrib

    @property
    def sourceline(self):
        return self.destination.sourceline

    def append(self, element):
        if isinstance(element, self.__class__):
            self.destination.append(element.destination)
        else:
            self.destination.append(element)

    @property
    def tail(self):
        return self.destination.tail

    @tail.setter
    def tail(self, value):
        self.destination.tail = value

    # General helpers
    @staticmethod
    def _copy_element(element):
        "Returns a copy of the 'element', stripping all its contents"
        new_element = deepcopy(element)
        for item in new_element:
            new_element.remove(item)
        return new_element
