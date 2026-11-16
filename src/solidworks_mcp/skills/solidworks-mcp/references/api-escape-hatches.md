# When no tool does it: the SolidWorks API itself

Use a tool whenever one exists. When none does the job (an indent, a
combine, an option no tool exposes), look the API call up, then run it as a
C# snippet.

## Look it up

- `search_solidworks_api_help`, `lookup_api_interface` and
  `lookup_api_method` give names, signatures and types, from the installed
  type library.
- `get_api_remarks` gives the help page: the summary, what each parameter
  means, and the Remarks, which hold the rules the signature doesn't show.
  `IFeatureManager.InsertIndent` wants the target body selected with mark 1
  and the tool regions with mark 4; `InsertCombineFeature` takes the main
  body with mark 1 and the tools with mark 2, then null and an empty array.
  Pass `example` as `csharp`, `vbnet` or `vba` for the help's own example
  code. Leave out `member` for the interface's page: how to get it, and its
  members. The first call builds the index from the installed
  `api\sldworksapi.chm` (about a minute, once) under
  `%LOCALAPPDATA%\solidworks_mcp\api_remarks\<year>`; with
  `build_if_missing` false it says how to build it instead
  (`python -m solidworks_mcp.tools.api_remarks`).
- `lookup_api_enum` gives the integers the API takes: `swBodyOperationType_e`
  lists SWBODYINTERSECT 15901, SWBODYCUT 15902 and SWBODYADD 15903;
  `swInsertPart` finds the swInsertPartOptions_e bits; `15902` finds the
  members with that value. Its index ships with the server and names the
  release it came from. SolidWorks keeps a member's value across releases,
  but an older release lacks the newer members.

## Run it

`run_api_snippet` compiles C# against SolidWorks' interop assemblies in its
own Windows PowerShell process, attaches to the running SolidWorks (it never
starts one) and calls `Run(SldWorks app)`.

- It is off unless the server's environment sets
  `SOLIDWORKS_MCP_ALLOW_API_SNIPPETS=1` (then restart the server). It acts on
  the user's live session and can't be undone from here, so ask the user
  before turning it on or running something that changes their model.
- Send a whole class with `public static object Run(SldWorks app)`, that
  method alone, or only its body. System, System.Collections.Generic,
  System.Linq, System.Runtime.InteropServices and the sldworks and swconst
  namespaces are already imported.
- The compiler is C# 5: no `$"..."` strings, `?.` or `nameof`; use
  `string.Format`.
- Check it with `compile_only` true first: that compiles it and finds `Run`
  without touching SolidWorks.
- What `Run` returns comes back as JSON in the reply's data: numbers,
  strings, arrays, dictionaries and anonymous objects; a COM object shows as
  `<COM IBody2>`. Console.WriteLine and Debug.Print output comes back as its
  stdout.
- `timeout_s` (60 s; at most 600) kills the snippet's process; a SolidWorks
  call it already started still finishes.
- If it opens or activates a document, call `activate_document` afterwards,
  so the other tools work on that one.
- A failure is `api_snippet_failed`. Its data says where it stopped: the
  compile errors (line numbers count from the snippet's first line), no
  `Run` found, or the exception (type, HRESULT, the snippet's line), with
  stdout and stderr. No running SolidWorks is `solidworks_unavailable`.

The active part's bodies, with their face counts:

```csharp
ModelDoc2 doc = (ModelDoc2)app.ActiveDoc;
if (doc == null || doc.GetType() != (int)swDocumentTypes_e.swDocPART)
    return "Open a part first.";
object[] bodies = (object[])((PartDoc)doc).GetBodies2((int)swBodyType_e.swSolidBody, true);
var found = new List<object>();
if (bodies != null)
{
    foreach (object item in bodies)
    {
        Body2 body = (Body2)item;
        found.Add(new { name = body.Name, faces = body.GetFaceCount() });
    }
}
return new { part = doc.GetTitle(), bodies = found };
```

After a snippet changes the model, read it back with the tools
(`list_features`, `get_model_info`, `verify_model`) before going on.
