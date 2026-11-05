const assert=require('node:assert/strict'),path=require('node:path'),{pathToFileURL}=require('node:url');
(async()=>{const {documentationHref}=await import(pathToFileURL(path.join(__dirname,'../tools/docs/links.mjs')));
assert.equal(documentationHref('API.md#overview'),'API.html#overview');
assert.equal(documentationHref('API.md?format=print#overview'),'API.html?format=print#overview');
assert.equal(documentationHref('https://github.com/dockur/windows/blob/master/docs/environment.md'),'https://github.com/dockur/windows/blob/master/docs/environment.md');
assert.equal(documentationHref('https://vendor.example/docs/source.md?raw=1#part'),'https://vendor.example/docs/source.md?raw=1#part');
assert.equal(documentationHref('//vendor.example/source.md'),'//vendor.example/source.md');
assert.equal(documentationHref('https://mykol.com/mykos/api/v2/'),'/mykos/api/v2/docs');
assert.equal(documentationHref('#current'), '#current');console.log('Local guide suffixes, query/fragment preservation, external Markdown URLs and historical API navigation passed.');})().catch(e=>{console.error(e);process.exitCode=1});
