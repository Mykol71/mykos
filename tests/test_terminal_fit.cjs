const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),path=require('path');
for(const file of ['index.html','terminal-window.html']){
 const html=fs.readFileSync(path.join(__dirname,'../services/web',file),'utf8');
 const code=html.slice(html.indexOf('function mykosFitTerminal('),html.indexOf('\n}',html.indexOf('function mykosFitTerminal('))+2);
 let resized;const viewport={offsetWidth:360,clientWidth:345},screen={getBoundingClientRect:()=>({width:626,height:240})};
 const root={querySelector:s=>s==='.xterm-screen'?screen:viewport};
 const surface={clientWidth:360,clientHeight:280};const term={element:root,cols:80,rows:24,resize:(cols,rows)=>resized={cols,rows}};
 const context=vm.createContext({getComputedStyle:e=>e===surface?{paddingLeft:'12px',paddingRight:'12px',paddingTop:'12px',paddingBottom:'12px'}:{},term,surface});
 vm.runInContext(code,context);vm.runInContext('mykosFitTerminal(term,surface,null)',context);
 assert.equal(resized.cols,40);assert.ok(resized.cols*(626/80)<360-24-15);assert.equal(resized.rows,25);
 resized=null;context.addon={fit(){throw Error('addon failed')}};vm.runInContext('mykosFitTerminal(term,surface,addon)',context);assert.equal(resized.cols,40);
 surface.clientWidth=600;vm.runInContext('mykosFitTerminal(term,surface,null)',context);assert.equal(resized.cols,71);
 resized=null;surface.clientWidth=0;vm.runInContext('mykosFitTerminal(term,surface,null)',context);assert.equal(resized,null);
 assert.ok(html.includes("url.searchParams.set('geometry','1')"));assert.ok(!html.includes("if(fitAddon)url.searchParams.set"));
}
console.log('Popup/inline terminal fitting: padding, scrollbar, addon failure, resize and hidden surface passed.');
