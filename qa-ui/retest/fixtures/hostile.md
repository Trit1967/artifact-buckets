# Hostile <script>window.__xss=1</script> heading

<script>window.__xss=2</script>
<img src=x onerror="window.__xss=3">
<iframe src="javascript:window.__xss=4"></iframe>
<a href="javascript:window.__xss=5">raw anchor</a>

[js link](javascript:window.__xss=6)
[JS upper](JaVaScRiPt:window.__xss=7)
[entity js](&#106;avascript:window.__xss=8)
[data link](data:text/html,<script>alert(1)</script>)
[vb](vbscript:msgbox)
[proto-rel](//evil.example/x)
[attr break](https://ok.example/" onmouseover="window.__xss=9)
[star in url](https://ok.example/*a) and later *b* text
![img onerror](x" onerror="window.__xss=10)
**bold [link](https://ok.example/_x_) _em_**
`<img src=x onerror=window.__xss=11>`

> quote level 1 &lt;img src=x onerror=window.__xss=12&gt;
> > nested quote <b>bold?</b>
> > > third level [q](javascript:window.__xss=13)

```html
<script>window.__xss=14</script>
```

| a | b |
|---|---|
| <img src=x onerror=window.__xss=15> | [t](javascript:x) |

- [ ] task <svg onload=window.__xss=16>
- [x] done

Literal &amp; and &lt;tag&gt; in a paragraph, and an email <mailto:a@b.c>.
