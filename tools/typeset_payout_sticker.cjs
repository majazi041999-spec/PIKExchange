// Native typography for the dense information-request card. The brand raster is
// placed unchanged; all copy, frame, geometry and shapes remain in this source.
const fs=require('node:fs/promises');
const path=require('node:path');
const sharp=require(process.env.SHARP_MODULE_PATH||'sharp');
const root=path.resolve('assets/stickers/redesign');
(async()=>{
  const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 512 512">
    <defs><linearGradient id="bg" x2=".9" y2="1"><stop stop-color="#04110d"/><stop offset=".55" stop-color="#020908"/><stop offset="1" stop-color="#092019"/></linearGradient><linearGradient id="edge" x2=".8" y2="1"><stop stop-color="#43efb1"/><stop offset=".4" stop-color="#12a477"/><stop offset="1" stop-color="#24e7a3"/></linearGradient><clipPath id="c"><rect x="7" y="7" width="498" height="498" rx="57"/></clipPath><filter id="g" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3"/></filter></defs>
    <rect x="8" y="8" width="496" height="496" rx="56" fill="none" stroke="#12c987" stroke-width="7" opacity=".65" filter="url(#g)"/>
    <rect x="7" y="7" width="498" height="498" rx="57" fill="url(#bg)" stroke="url(#edge)" stroke-width="2.5"/>
    <g clip-path="url(#c)" fill="none" stroke="#38514d" stroke-width="2" opacity=".42"><path d="M0 20 140 160M30 0 0 30m0 100 130-130M512 15 370 157M490 0l22 22m-140-22 140 140M0 368l144 144M0 485 135 350m240 162 137-137m-20 137-129-129"/><path d="m0 65 67 67m378 313 67 67" stroke="#54bc98"/><circle cx="66" cy="86" r="8" fill="#175240"/><circle cx="446" cy="86" r="8" fill="#175240"/><circle cx="66" cy="446" r="8" fill="#175240"/><circle cx="446" cy="446" r="8" fill="#175240"/></g>
    <rect x="30" y="163" width="452" height="303" rx="24" fill="#03100c" opacity=".8"/>
  </svg>`;
  await fs.writeFile(path.join(root,'source/payout_details.svg'),svg);
  const logo=await sharp('assets/banks/source/pik.png').trim({background:'#00000000',threshold:1}).resize(184,100,{fit:'inside'}).png().toBuffer();
  const lm=await sharp(logo).metadata();
  // The composition is rendered at 2x for clean downsampling into Telegram's 512px.
  const overlays=[];
  const doubledLogo=await sharp(logo).resize(lm.width*2,lm.height*2).png().toBuffer();
  overlays.push({input:doubledLogo,left:Math.round((1024-lm.width*2)/2),top:62});
  const catalog=JSON.parse(await fs.readFile(path.join(root,'catalog.json'),'utf8'));
  const lines=catalog.stickers.find(x=>x.key==='payout_details').lines;
  const ys=[334,411,523,622,721,820];
  for(let i=0;i<lines.length;i++){
    const size=i<2?31:27;
    const color=i<2?'#f4fff9':'#79f8c1';
    const text=await sharp({text:{text:`<span foreground="${color}" size="${size*2*1024}">${lines[i]}</span>`,font:'Vazirmatn ExtraBold',fontfile:path.join(root,'Vazirmatn-ExtraBold.ttf'),width:880,align:'centre',rgba:true,dpi:72}}).png().toBuffer();
    const m=await sharp(text).metadata();
    if(m.height>98||m.width>900)throw Error('Text does not fit line '+i);
    overlays.push({input:text,left:Math.round((1024-m.width)/2),top:ys[i]});
  }
  await sharp(Buffer.from(svg)).composite(overlays).png().toFile(path.join(root,'source/payout_details.png'));
})();
