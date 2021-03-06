import codecs, json, os, markdown, csv, re, shutil
from StringIO import StringIO
from datetime import datetime

class UTF8Recoder:
    """
    Iterator that reads an encoded stream and reencodes the input to UTF-8
    """
    def __init__(self, f, encoding):
        self.reader = f

    def __iter__(self):
        return self

    def next(self):
        val = self.reader.next()
        raw = val.encode("utf-8")
        if raw.startswith(codecs.BOM_UTF8):
            raw = raw.replace(codecs.BOM_UTF8, '', 1)
        return raw

class UnicodeReader:
    """
    A CSV reader which will iterate over lines in the CSV file "f",
    which is encoded in the given encoding.
    """

    def __init__(self, f, dialect=csv.excel, encoding="utf-8", **kwds):
        f = UTF8Recoder(f, encoding)
        self.reader = csv.reader(f, dialect=dialect, **kwds)

    def next(self):
        row = self.reader.next()
        return [unicode(s, "utf-8") for s in row]

    def __iter__(self):
        return self

class BuildException(Exception):
    pass

class Command(object):
    def __init__(self, raw, file_cfg, config):
        self.config = config
        self.file_cfg = file_cfg

        raw = raw.strip()
        raw = raw.replace("\n", " ").strip()
        boundary = raw.find(" ")
        self.command = raw[:boundary].strip()

        # inline = raw[boundary:].replace("\n", " ").strip()
        sargs = StringIO(raw[boundary:])
        args = UnicodeReader(sargs).next()

        def listify(val):
            if "|" in val:
                vals = [v.strip() for v in val.split("|")]
                val = vals
            return val

        self.args = []
        self.kwargs = {}
        for arg in args:
            if "=" in arg:
                bits = arg.split("=")
                val = listify(bits[1].strip())
                self.kwargs[bits[0].strip()] = val
            else:
                val = listify(arg.strip())
                self.args.append(arg.strip())

    def run(self):
        return COMMANDS[self.command](self.file_cfg, self.config, *self.args, **self.kwargs)

    def _parse(self, raw):
        pass

def run(config):
    od = config.get("out_dir")
    files = config.get("files", [])
    for file_cfg in files:
        of = file_cfg.get("out")
        out = os.path.join(od, of)

        if file_cfg.get("process", False):
            text = build(file_cfg, config)
            with codecs.open(out, "wb", "utf-8") as f:
                f.write(text)
        else:
            src_dir = config.get("src_dir")
            root = file_cfg.get("root")
            infile = os.path.join(src_dir, root)
            shutil.copy(infile, out)

def build(file_cfg, config, phases=None):
    if phases is None:
        phases = ["expand", "number", "integrate", "render"]
    src_dir = config.get("src_dir")
    root = file_cfg.get("root")
    root = os.path.join(src_dir, root)

    if "expand" in phases:
        text = expand(root, file_cfg, config)
        fn = os.path.basename(file_cfg.get("root"))
        with codecs.open(os.path.join("tmp", fn), "wb", "utf-8") as f:
            f.write(text)
    if "number" in phases:
        text = number(text, file_cfg, config)
    if "integrate" in phases:
        text = integrate(text, file_cfg, config)
    if "render" in phases:
        text = render(text, file_cfg, config)

    return text + "\n\n"

def expand(path, file_cfg, config):
    out = ""
    with codecs.open(path, "rb", "utf-8") as f:
        lines = f.readlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if line == "":
                return out
            if line.startswith("{%"):
                current_i = i
                line, i = _read_command(i, line, lines)
                command = Command(line, file_cfg, config)
                if command.command in EXPAND_COMMANDS:
                    out += command.run()
                else:
                    i = current_i
                    line = lines[i]
                    out += line
            else:
                out += line
            i += 1
    return out

def number(text, file_cfg, config):
    # now number the sections
    numbered = ""
    lines = text.split("\n")
    for line in lines:
        if line.startswith("#"):
            line = _number_header(line, file_cfg, config)
        numbered += line + "\n"
    return numbered

def _read_command(idx, first_line, lines):
    command = first_line.strip()
    while not command.endswith("%}"):
        idx += 1
        command += " " + lines[idx].strip()
    return command[2:-2].strip(), idx

def _number_header(line, file_cfg, config):
    level = 0
    for c in line:
        if c == "#":
            level += 1
        else:
            break

    if "current_header" not in file_cfg:
        file_cfg["current_header"] = [0,0,0]
    current_header = file_cfg.get("current_header")

    if level == 0:
        return line
    elif level == 1:
        current_header[0] += 1
        current_header[1] = 0
        current_header[2] = 0
    elif level == 2:
        current_header[1] += 1
        current_header[2] = 0
    elif level == 3:
        current_header[2] += 1
    else:
        return line

    num = ""
    for x in current_header:
        if x > 0:
            num += str(x) + "."

    return "#" * level + " " + num + " " + line[level:].strip()

def integrate(text, file_cfg, config):
    cmd_rx = "(\{%.+?%\})"
    m = re.search(cmd_rx, text, re.DOTALL)
    while m is not None:
        matched = m.group(0)
        command = Command(matched[2:-2].strip(), file_cfg, config)
        rep = command.run()
        text = text.replace(matched, rep)
        m = re.search(cmd_rx, text, re.DOTALL)
    return text

def render(text, file_cfg, config):
    return markdown.markdown(text, extensions=["markdown.extensions.tables"])

##################################################
# Commands

def include(file_cfg, config, path):
    nfc = {"root" : path}
    if "current_header" in file_cfg:
        nfc["current_header"] = file_cfg["current_header"]
    text = build(nfc, config, phases=["expand"])
    if "current_header" in file_cfg:
        file_cfg["current_header"] = nfc["current_header"]
    return text

def date(file_cfg, config, thedate, format):
    dv = None
    if thedate == "now":
        dv = datetime.utcnow()

    if dv is not None:
        return dv.strftime(format)

    raise BuildException()

def ref(file_cfg, config, reference):
    ref_file = config.get("resources", {}).get("ref")
    with codecs.open(ref_file, "rb", "utf-8") as f:
        reader = UnicodeReader(f)
        for row in reader:
            if row[0] == reference:
                return "[[" + row[0] + "](#" + _anchor_name(row[0]) + ")]"

    raise BuildException()

def table_rows_as_paras(file_cfg, config, source, links=None, bold=None, anchor=None):
    bd = config.get("src_dir")
    path = os.path.join(bd, source)
    paras = []
    with codecs.open(path, "rb", "utf-8") as f:
        reader = UnicodeReader(f)
        headers = reader.next()
        for row in reader:
            para = ""
            for i in range(len(row)):
                col = row[i]
                name = headers[i]

                a = ""
                if name == anchor and anchor is not None:
                    a = '<a name="' + _anchor_name(col) + '">'

                if name == links and links is not None:
                    col = "[" + col + "](" + col + ")"
                if name == bold and bold is not None:
                    col = "**" + col + "**"

                para += a + col + " "
            paras.append(para)
    return "\n\n".join(paras)

def dl(file_cfg, config, source, term, definition, link=None):
    bd = config.get("src_dir")
    path = os.path.join(bd, source)
    frag = "<dl>"
    with codecs.open(path, "rb", "utf-8") as f:
        reader = UnicodeReader(f)
        headers = reader.next()

        for row in reader:
            dt = None
            dd = None
            for i in range(len(row)):
                col = row[i]
                name = headers[i]
                if name == link:
                    col = "[" + col + "](" + col + ")"
                if name == term:
                    dt = col
                    continue
                if name == definition:
                    dd = col
                    continue
            dt = markdown.markdown(dt)[3:-4]    # removes the <p> and </p> markdown inserts
            dd = markdown.markdown(dd)[3:-4]    # removes the <p> and </p> markdown inserts
            frag += "<dt>" + dt + "</dt><dd>" + dd + "</dd>"
    frag += "</dl>"
    return frag

def table(file_cfg, config, source):
    bd = config.get("src_dir")
    path = os.path.join(bd, source)
    frag = ""
    with codecs.open(path, "rb", "utf-8") as f:
        reader = UnicodeReader(f)
        headers = reader.next()
        frag = "| " + " | ".join(headers) + " |\n"
        frag += "| " + "--- |" * len(headers) + "\n"
        for row in reader:
            linified_row = [cell.replace("\n", "<br>") for cell in row]
            frag += "| " + " | ".join(linified_row) + " |\n"
    return frag


def openapi_list_descriptions(file_cfg, config, source, field, keys=None):
    bd = config.get("src_dir")
    path = os.path.join(bd, source)
    frag = ""
    with codecs.open(path, "rb", "utf-8") as f:
        api = json.loads(f.read())
        bits = field.split(".")
        for bit in bits:
            api = api.get(bit)
        if keys is not None:
            if not isinstance(keys, list):
                keys = [keys]
            for key in keys:
                desc = api.get(key).get("description")
                frag += " * " + desc + "\n"
    return frag

def url(file_cfg, config, relpath):
    base = config.get("base_url")
    return base + relpath

def openapi_paths(file_cfg, config, source, path_order=None, method_order=None, header_depth=1):
    if method_order is None:
        method_order = ["get", "post", "put", "delete"]
    header_depth = int(header_depth)
    bd = config.get("src_dir")
    path = os.path.join(bd, source)
    frag = ""
    with codecs.open(path, "rb", "utf-8") as f:
        api = json.loads(f.read())
        paths = api.get("paths")
        path_names = paths.keys()
        if path_order is not None:
            path_names = ["/" + po for po in path_order]
        for pn in path_names:
            path_info = paths.get(pn)
            if path_info is None:
                continue
            for method in method_order:
                method_info = path_info.get(method)
                if method_info is None:
                    continue
                frag += _render_method_info(header_depth, pn, method, method_info)
    return frag

def _render_method_info(header_depth, path_name, method, method_info):
    frag = "#" * header_depth + method.upper() + " " + path_name[1:] + "\n"
    return frag

def _anchor_name(v):
    v = v.lower().strip()
    return v.replace(" ", "_")

############

COMMANDS = {
    "include" : include,
    "ref" : ref,
    "date" : date,
    "table_rows_as_paras" : table_rows_as_paras,
    "dl" : dl,
    "table" : table,
    "openapi_list_descriptions" : openapi_list_descriptions,
    "url" : url,
    "openapi_paths" : openapi_paths
}

EXPAND_COMMANDS = ["include", "openapi_paths"]

##################################################

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config", help="config file for build")
    args = parser.parse_args()

    config = None
    with codecs.open(args.config) as f:
        config = json.loads(f.read())
    run(config)