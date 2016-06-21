from __future__ import absolute_import

from lxml import html

from ..handlers import Handler
from ..strings import OpenString
from ..transcribers import Transcriber


class HTMLHandler(Handler):
    """ Overly optimistic proof-of-concept for HTML handler that respects the
        mistakes in the formatting of the source file. The main logic is:

        1. Put (perhaps broken) HTML file into `lxml.html.fromstring`
        2. Go through using lxml's tools to find and extract strings into a
           python structure (stringset)
            - Try to make sure the extracted parts are **exactly** as they are
              in the file
            - Use the elements' XPATH specifier as the extracted strings' key
        3. Go through the file again (with python, no lxml) and start replacing
           extracted strings with hashes
        4. Save the result as the template (if it's a source file)

       For this proof-of-concept, we only consider <p> tags as holding actual
       content.

    """

    name = "HTML"
    extension = "html"

    def parse(self, content, **kwargs):
        root = html.fromstring(content, parser=html.HTMLParser(recover=False))
        tree = root.getroottree()
        stringset = []
        for p in root.xpath('//p'):
            stringset.append(OpenString(tree.getpath(p), p.text))

        transcriber = Transcriber(content)
        stringset_iter = iter(stringset)
        search_from = 0
        try:
            while True:
                string = next(stringset_iter)
                try:
                    position = content.index(string.string, search_from)
                except ValueError:
                    break
                else:
                    transcriber.copy_until(position)
                    transcriber.skip(len(string.string))
                    transcriber.add(string.template_replacement)
                    search_from = position + len(string.string) + 1
        except StopIteration:
            pass
        transcriber.copy_until(len(content))
        return transcriber.get_destination(), stringset

    def compile(self, template, stringset):
        transcriber = Transcriber(template)
        stringset = iter(stringset)
        search_from = 0
        try:
            while True:
                string = next(stringset)
                try:
                    position = template.index(string.template_replacement,
                                              search_from)
                except ValueError:
                    break
                else:
                    transcriber.copy_until(position)
                    transcriber.skip(len(string.template_replacement))
                    transcriber.add(string.string)
        except StopIteration:
            pass
        transcriber.copy_until(len(template))

        return transcriber.get_destination()
