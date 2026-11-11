# 执行摘要

**分形（fractal）**概念非常宽泛，缺少唯一定义，常见定义是：**Hausdorff维数**严格大于其**拓扑维数**。但这只是经验定义，并非必要充分条件。严格数学角度，分形对象可以是极限集、吸引子、曲线、测度等；而生成分形的方法包括几何递归删减、自相似迭代（IFS）、符号替换（L-system）、动力学迭代（逃逸时间映射或混沌系统）等，二者要分清。我们不能把每次迭代有限次的图形误认为分形极限，也不能把布尔运算当作通用生成方法。分类上，我们可以从**对象性质**和**生成机制**两层拆分：前者包括自相似集、分形曲线、自仿射磁贴、混沌吸引子、随机集、统计分形等；后者包括递归删减、边替换/节点替换、L-system、IFS（含Graph-directed）、复数进制生成、逃逸迭代、ODE混沌模拟等。各类别有交叉：例如同一个**谢尔宾斯基三角**既是删减生成的点集，也是L-system曲线，也可用IFS或Chaos Game产生；而**海威龙曲线**（Heighway dragon）与**双龙（Twin dragon）**是不同的分形，有不同的生成规则。

以下内容按分类系统展开，内容**中文撰写**，关键术语并列英文。每类介绍经典对象与生成算法，并给出数学公式或伪代码。对于用户提及的分形，也在对应类别内讨论。最后给出具体算法构造示例，对同一分形的多种构造做比较。给出**参考文献与优质资源**建议，包括教材、论文、视频和书籍。

## A. 基本定义与分类

**分形（Fractal）**一词由Mandelbrot提出，涵义不唯一。Mandelbrot的经验定义是：集合的Hausdorff维数严格大于其拓扑维数。例如，经典的**Koch曲线**拓扑维为1，但Hausdorff维为$\log4/\log3\approx1.2629$，满足定义。**分形并非严格要求维数非整数**，只是强调“更粗糙、更碎片化”的几何结构。而“混沌”强调动力系统的对初始值敏感性和不稳定轨迹，并非几何概念；混沌系统的吸引子常具有分形维数，但混沌和分形不是等价概念。

我们区分几个概念层级（见表）：

- **分形对象（Fractal Object）**：数学上的极限集、曲线、边界、测度等。如Cantor集（无处稠密点集）、谢尔宾斯基三角（点集）、Koch曲线（连通曲线）、曼德尔布罗集（复平面参数吸引子）、分形测度（Cantor测度）等。  
- **生成机制（Construction Mechanism）**：描述分形构造的数学过程，如“每级删除中间部分”、“重复进行相似映射”、“替换边为折线”、“解迭代微分方程获得混沌轨道”等。  
- **数值算法（Numerical Algorithm）**：具体实现分形的程序方法，如递归迭代、Chaos Game随机迭代、逃逸时间算法、ODE积分仿真等。注意一个正确生成**极限集**的算法应该拥有极限意义，例如I 函数系统迭代收敛到不动吸引子或递归到无限细节。常见**误解**：把“做有限次迭代后生成的图像”当作分形本身；或者把简单的布尔几何运算当成分形生成规则（布尔只是实现技术）。  
- **渲染编码（Rendering）**：将数学对象视觉化的方式，如矢量曲线绘制、点云绘制、栅格逃逸图、GPU着色、体素网格化等。渲染不改变分形本身，只影响显示效果。

### 分类结构

我们给出一个分类树，从数学机理角度分组，并列出主要代表对象和方法：

- **几何递归 / 删减（Geometric Recursion / Cut-away）**：从初始形状开始，按规则删除或保留子块，每级产生子结构。典型：Cantor集、Smith–Volterra–Cantor（肥Cantor）、谢尔宾斯基三角/地毯/四面体、Vicsek集、Menger海绵、Apollonian垫片等。生成：**保持子块并集**或**总体减去删除部分**。两者数学上等价于保留集的自相似；绘制上常直接构造保留集（几何并集）或用布尔差集模拟穴洞。

- **边替换 / 曲线递归（Edge Replacement / Substitution Curves）**：以线段（边）为单元，每级用更复杂的折线替换一条线段，生成连续曲线。典型：Koch雪花曲线、Minkowski岛、Lévy C曲线、Heighway龙、Gosper曲线、Hilbert曲线、Peano曲线、Sierpiński箭头曲线等。形式上可用**递归边替换规则**或**L-系统**实现。  
  - **Lindenmayer系统（L-system）**是一种符号重写系统（形式文法），先在字符串层面递归替换符号（代表直线F、转向+/-等），再由Turtle绘制。例如Koch曲线规则：$F\to F+F--F+F$，角度60°。Prusinkiewicz & Lindenmayer等系统地介绍了L-system在植物和分形中的应用。  
  - **纸张折叠（Paperfolding）**：如龙曲线也可通过模拟不断折纸而成的二进制转向序列生成。Heighway龙与纸折曲线产生相同极限轨迹。  
  - 注意区分**Heighway龙曲线**（Harter–Heighway Dragon）与**双龙（Twin Dragon）**、**裂龙（Terdragon）**、**Lévy龙**等不同变种，它们的迭代规则和极限路径不一样，不可混用名称。

- **节点替换 / 分支（Node Replacement / Branching / Fractal Trees）**：从一个点或形状出发，不是替换边，而是在端点或节点处生成若干新节点和边。例如H-树、T-方块、毕达哥拉斯树、分形树（Fractal Tree）等。常见用L-system带分枝的方式实现，每个叶端可能生出多条新分支。节点替换通常需要**栈**（倒退机制）或递归来保留形状拓扑。

- **迭代函数系统（IFS, Hutchinson 1981）**：给定一组**收缩仿射映射**$S_i(x)=A_i x + b_i$，反复作用于任意初始点集$A$, 通过Hutchinson算子$S(A)=\bigcup_iS_i(A)$，存在唯一吸引子$K=S(K)$。满足压缩映射条件的IFS总有**全局不变集**$K$。在欧式空间中，IFS吸引子常是著名自相似集，如谢尔宾斯基三角、Cantor集、Mandelbrot集边界等可由IFS产生。特别地，**随机迭代算法（Chaos Game）**通过随机选取映射$S_i$迭代点$p_{n+1}=S_{i_n}(p_n)$，样本点集合渐近IFS吸引子的顶点覆盖（伊菲尔德等人证明几乎必收敛）。IFS理论给出**维数公式**：若映射$S_i$的缩放比为$r_i$，满足开集条件（OSC）下，维数$s$满足Moran方程$$\sum_i r_i^s=1$$。例如Cantor集$s$由$2(1/3)^s=1$得$s=\log2/\log3$。

- **图导IFS（Graph-directed IFS）**：Mauldin–Williams等推广，用带权有向图描述IFS，每个状态$i$对应一个集$K_i=\bigcup_{j, i\to j}(A_{ij}K_j+b_{ij})$，可生成多元分形或子集分离。所示Hutchinson的地址图形式即属于此类。

- **自仿射磁贴与数制分形**：结合算术代数，用**复数基数**或整数基数展开展示分形。典型例子是**双龙磁贴（Twin Dragon)**：基于复数基底$\beta=1+i$，数字集$\{0,1\}$的迭代地址集。公式$z=\sum_{k\ge1} d_k \beta^{-k}$构成磁贴。Kenyon、Knuth等人研究过这些。其他如带对称格点的数字系统（高斯整数$\mathbb Z[i]$、艾森斯坦整数$\mathbb Z[\omega]$）生成的**Self-affine tiles**。与IFS不同，这些磁贴平铺性质需要代数限制（替代关系和邻域规则）。**图导IFS**也可用于此（各顶点状态对应不同数字或方向）。

- **复动力学逃逸时间（Complex Dynamics / Escape-time）**：研究复二次（及更高）多项式迭代行为的分形，如曼德尔布罗集、Julia集、多项式布林格分形等。定义例如$z_{n+1}=z_n^2+c$，从初始值或参数$c$迭代，研究轨道是否逃逸到无穷。曼德尔布罗集是参数$c$使$0$轨道不逃逸的集合，Julia集为固定$c$下初始点集合的分界。可通过**逃逸时间着色**（escape-time coloring）可视化轨道收敛速度。Burning Ship、Newton法分形（迭代$f(z)=z^2-1$等求根）、双分支小波等变体亦归此类。

- **一维动力学与分岔（1D Maps and Bifurcations）**：如Logistic映射$x_{n+1}=r x_n(1-x_n)$、Tent映射、分数分形映射等。在参数$r$调变过程中出现周期倍增、分岔、混沌。其分形对象包括不动点吸引子集合、吸引子分形结构（Feigenbaum常数等）、参数图（分岔图）等。关联概念：符号动力学、Smale马蹄映射（Horseshoe map）等。需区分参数图（如二维的$bifurcation diagram$不是集合的集合，更多是系统行为总结）与状态空间图（轨道分布）。

- **连续时间混沌物理系统（Continuous Chaos / Physics）**：经典例子Lorenz系统、Rössler系统、双摆(Double Pendulum)、粒子混沌散射等。它们的分形表现主要是**吸引子**、**基线（basin boundary）**或相空间截面**不交集**（chaotic sea vs stable islands）。例如**双摆**：由拉格朗日方程导出二阶ODE，四维相空间。常取二维截面（如双摆角的初值）绘制相图，每点记录有限时间的离散化后摇摆角度或分离率以色彩显示，可见混沌海（颜色剧烈变化）和稳定岛（颜色规则）分布。重要的是：这种绘图显示了非线性动力的敏感性和分形结构，但**严格的Hausdorff分形**需在极限中定义（如无穷时演化或完全收敛态）。逃逸时间和末态角着色是常用方法。

- **随机分形（Random Fractals）**：通过随机过程生成。典型：**布朗运动**轨迹（1/2维不可微曲线）、**分形布朗运动（fractional Brownian motion）**、**DLA（扩散限制聚集）**、**分形降雨聚集**、**自相似随机集**（随机Cantor集、分形电线网）等。区别于严格自相似，这些**统计自相似**，只有在统计意义上体现尺度不变性。常用特征有幂律衰减、统计维数和多重分形谱。绘制策略多为随机生成采样。

- **分形曲面与自然分形（Fractal Surfaces / Natural Phenomena）**：如地形生成（midpoint displacement / diamond-square算法）、云雾纹理（层级噪声）、海岸线模拟、多重分形模拟天气/湍流。严格数学分形一般指无限层级结构，现实自然多使用**分形噪声**或有限标度区间的自相似模型。需要注意区分数学意义的分形（绝对无穷细）与物理过程中的统计自相似建模。

- **圆反演与Kleinian群（Circle Inversion / Kleinian Groups）**：利用反演几何生成分形，如**阿波罗尼乌斯垫片（Apollonian gasket）**由无穷多个互相外切的圆构成，通过Descartes圆定理或Möbius变换迭代生成；**Ford圆、Soddy球**等。基于圆/球接触的分形通常涉及群反演和几何构造，不是仿射IFS。  
- **其他典型分形（Other Examples）**：Weierstrass函数（处处连续不可微函数）、Takagi曲线、索尔诺夫斯基曲线、Devil’s staircase（Cantor函数）、黎曼级数图像分形（Riemann函数）、多重分形谱等。以及一些构造性的分形插值函数、嵌套几何等等。

各类并非互斥。有很多交叉：IFS吸引子往往同时可由几何递归方式理解；L-system曲线可看作边替换的符号化版本；动力系统吸引子通常具有自相似特征；复杂基数分形在IFS框架下也可解释（图导IFS）；ODE混沌的相空间切片可通过“迭代地图”理解，类似于随机IFS。

## B. 经典分形与生成方法逐类介绍

### I. 几何递归 / 删减 (Geometric Recursion / Cut-Away)

这些分形由**初始形状**通过每级删除（或保留）固定子部分得到。数学上两种等价表达：**“并集表达”**（先并出所有保留块的集合）与**“差集表达”**（从整体扣除删除块）；两者在极限上产生相同集合，但几何建模时实现细节不同。

- **Cantor 集**：一维线段[0,1]每级删除中间三分之一，最终余下$C=\bigcap_{n=1}^\infty C_n$。Hausdorff维$s=\log2/\log3\approx0.6309$。此集满足自相似映射$S_0(x)=x/3,\;S_1(x)=x/3+2/3$使$C=S_0(C)\cup S_1(C)$。  
- **Smith–Volterra–Cantor 集（肥Cantor）**：类似Cantor删减，但留下正的Lebesgue测度（例如每级删除更小区间，总长度不至0）。其Hausdorff维也<1，但可达1。  
- **谢尔宾斯基三角 (Sierpiński gasket)**：以三角形为起始，每级将每个小三角形挖去中间反向正三角形，保留3个顶角的3个三角形。等价IFS：3个仿射变换收缩比1/2。Hausdorff维$=\log3/\log2\approx1.585$。面积趋零（每级剩余面积$3/4$比例）。**注意**：这与“*Sierpiński曲线*”不同，后者一般指Sierpiński箭头曲线，是连续填充同一图形的路径。  
- **谢尔宾斯基地毯 (Sierpiński carpet)**：以正方形为始，每级9等分并删除中心小方块，保留8个块。等价IFS：8个缩放因子$1/3$仿射。维数$\log8/\log3\approx1.8928$，面积趋0。可推广到$n$维Menger海绵（见下）。  
- **Vicsek 分形**：初始正方形，3×3格中保留中心和四角共5个等大小方块，其它4个边中心块删除，每次迭代对每个保留块重复同样操作。IFS可用5个收缩映射$r=1/3$，维数$\log5/\log3\approx1.465$。这类似十字形集。**与T-方块**相近但删减方案不同。  
- **Menger 海绵 (Menger Sponge)**：三维类Carpet。以立方体为始，27等分后删除中央1个小立方和六个面中心的小立方（保留20个）。迭代过程是保留20个1/3缩放的立方体。整体集合维数$\log20/\log3\approx2.727$。也可等效地视作从立方体中减去7个小立方。**注意**：数学上只关心保留部分的极限集合，两种描述等价。但建模时若要生成网格表面，可能需要Boolean差集操作来挖洞。  
- **Sierpiński 四面体/八面体**等高维推广：例如四面体迭代保留4个顶角的子四面体，维数$\log4/\log2=2$。  
- **Cantor dust**：高维Cantor集的产物，如二维中以Cantor集横纵直积生成的“尘状”矩阵。  
- **Fractal T-Tetromino(四连块)**：自相似保留方式在T形块重复。  
- **Apollonian 垫片 (Apollonian gasket)**：起始三互相外切圆（或三个圆+围成大圆），递归填充空隙插入最大可能外切圆。由Descarte定理可解析生成新的圆。最终形成无限圆堆。并不是简单线性IFS，但可视为由Moebius变换生成。**维度**约为1.3057（平面中圆边界总长）。  

以上对象通常满足自相似开集条件（OSC），维数由Moran公式确定。每级仍是有限并集（连通分离部分），极限是Cantor型集合或Cantor尘。

### II. 边替换 / 符号动力学 / 空间填充曲线 (Edge Replacement / Substitution Curves / Space-Filling Curves)

这类关注**连通曲线**（拓扑维1）通过替换规则生成更复杂结构。主要工具是**替换曲线（substitution curve）**和**L-系统**。也涉及**符号动力学**（用符号串和推导系统定义迭代规则）。

- **Koch 雪花/Koch曲线**：Koch曲线初始为线段，规则为将线段分为三段、中间段凸起等边三角顶点。L-系统表示：公用符号$F$表示前进，规则$F\to F+F--F+F$,角度$60^\circ$。迭代极限是连续曲线，Hausdorff维$\log4/\log3\approx1.2629$。**雪花**是三条Koch曲线围成闭环。  
- **Minkowski 扩散(岛)/Minkowski sausage**：将线段替换为右角阶梯状折线，规则形如$F\to F-F+F+F-F+F$,角度$90^\circ$。维度$\log7/\log4\approx1.4037$。形似锯齿。  
- **Lévy C 曲线**：线段替换为一个等腰直角三角形边形（形似倒置直角三角形脚边）。L-系统：$F\to F+G, G\to F-G$,角度$90^\circ$（$F,G$为直线段符号）。无限细后呈平滑曲线，维2。  
- **Heighway 龙 (Heighway dragon)**：经典纸折曲线。L-系统可表达为$F\to F+G, G\to F-G$。等效于每级将一对相邻边折叠$90^\circ$生成。拓扑维1，Hausdorff维2。  
- **纸折曲线 (Paperfolding curve)**：通过模拟连续折纸产生指令序列，可生成同Heighway龙相同的极限轨迹。  
- **双龙 (Twin dragon, Davis–Knuth dragon)**：由Knuth等在数字表示中发现。其生成规则与Heighway不同：通常用收缩比$1/\sqrt2$的复数映射$z\to(1+i)^{-1} z$的两种旋转迭代得到。图形上是两个Heighway龙叠合成自相似镶嵌。维数2。**切勿混淆**Heighway龙与双龙，这两者吸引子不同。  
- **Terdragon (三折龙)**：迭代过程中每条边用三角折线替换（三个$60^\circ$折角序列），相当于黄金分割等分旋转，对称性不同。  
- **Gosper曲线 (Flowsnake)**：特殊60°角替换曲线，也称Gosper岛线，用两条规则替换边，可填充平面。可由L-系统或IFS生成。  
- **Hilbert 曲线**：二维空间填充曲线，先将线段替换成“H”形折线，迭代后连通填充整个正方形。L-系统有规则，拓扑上填充整个区域，维数2。  
- **Peano 曲线**：类似Hilbert，用递归模式遍历正方形空间，形成连续曲线。  
- **Sierpiński 箭头 (Sierpiński arrowhead curve)**：以三角形为基构建路径，类似Koch雪花的曲线路径版，能一步画出谢尔宾斯基地毯的结构而不取面积。  
- **Moore curve**：Hilbert曲线变形，可获得连续闭合填充曲线。  

**L-系统**：由A. Lindenmayer于1968年提出，擅长描述自相似图形。基本形式：**图形字母表**（如$\{F,G\}$）、**重写规则**、**起始字符串**、**转角定义**（Turtle绘图法）。Prusinkiewicz & Lindenmayer等书详细介绍。对于分形生成，L-系统的作用是**把递归替换转化为符号操作**，然后用“Turtle”规则解释。这和直接边替换等价：只要规则和角度一致，L-system和边替换法生成相同曲线。**伪代码示例（L-system）**：
```
axiom = "F"
rules = {"F": "F+F--F+F"}
angle = 60°
for iter in 1..n:
    newstring = ""
    for char in axiom:
        if char in rules: newstring += rules[char]
        else: newstring += char
    axiom = newstring
Turtle.draw(axiom)
```
堆栈控制（如`[` `]`保存/恢复）可实现分支树。

边替换和L-system属于**deterministic 文法替换**。也有**随机替换**：概率上下文无关L-system（stochastic L-system）可产生更丰富纹理，但核心类似。

### III. 迭代函数系统（IFS）与Chaos Game

**Hutchinson算子**：给定有限收缩映射族$S=\{S_1,\dots,S_N\}$，每$S_i(x)=r_i R_i x + t_i$（$r_i<1$）。定义映射作用$W(A)=\bigcup_i S_i(A)$。Hutchinson定理保证存在唯一**不动紧致集**$K$满足$K=W(K)$，即IFS吸引子。且$K$为$S_i$的不动点集闭包，每点可由无穷地址$\alpha=(i_1,i_2,\dots)$映射定义。

- **确定性迭代**：从任意非空紧集$A_0$迭代$A_{n+1}=W(A_n)$，在Hausdorff度量下$A_n\to K$。数值上可递归生成$W^n(A_0)$逐渐逼近$K$。
- **随机迭代（Chaos Game）**：随机选择映射$S_i$依概率$p_i$迭代点$p_{n+1}=S_{i_n}(p_n)$。若初值$p_0$任取，生成序列$\{p_n\}$随机漫步在$K$上，几乎肯定疏密分布于整个$K$。该方法收敛速度快，常用于绘制自相似图像。注意：对一个$I\!F\!S$，概率选择只是采样方式，不改变极限集。**特殊规则**：某些IFS要**禁选限制**（如Sierpinski Chaos Game中禁止连续选同一顶点，否则不会收敛）。

常见应用：
- **谢尔宾斯基三角**：3个仿射映射$S_i(x)=\frac12(x-a_i)$将起始点映射到各顶点$a_i$的中点处。Chaos Game：每次移动到当前点与随机顶点中点，可得到三角吸引子。
- **Cantor 集**：2个线性映射$x\to x/3,\;x\to x/3+2/3$。 
- **Menger 海绵**：20个$\tfrac13$收缩映射生成3D吸引子。
- **Barnsley 蕨叶**：4个仿射映射，加不同概率，生成蕨叶图案。公式见Barnsley(1988)。  
- **Vicsek 集**：5个收缩映射对应十字结构。  
- **TwDragon, Gosper Curve**等可视作自仿射磁贴的一维例子（也符IFS的形式）。

**图导IFS**（Graph-Directed IFS，Mauldin-Williams 1988）：用有向图$G$连接多个状态，每条边$e:i\to j$带仿射映射$S_e$。系统$K_i=\bigcup_{e:i\to j} S_e(K_j)$产生一组分形集，常见于多维指标的磁贴和某些替代分形的描述。

重要定理：**吸引子维数**。若$S_i$为相似映射（保持角度），Hutchinson给出$\sum r_i^s=1$解$s$等于$K$的Hausdorff维（需要OSC）。如Cantor集用2个$r=1/3$映射得到$s=\log2/\log3$；Sierpiński三角3个$r=1/2$得$s=\log3/\log2$。若有重合或自仿射映射（非等向），一般需Falconer等深入分析。

### IV. 自仿射磁贴与数制分形 (Self-affine Tiles and Numeration)

利用整数基数展开生成分形，是IFS的一个代数化特例。**核心思想**：选定复数$\beta$（复基），选“数字”集合$\mathcal D\subset\mathbb Z[\beta]$，考虑所有$\sum_{k=1}^\infty d_k\beta^{-k}$组合形成的点集。这定义了一个自仿射磁贴，满足IFS形式
$$T=\bigcup_{d\in\mathcal D}\beta^{-1}(T+d).$$
经典例子：
- **Twin Dragon**：$\beta=1+i, \mathcal D=\{0,1\}$。结果是一块单连通区域能平铺复平面。值得注意：Twin Dragon是二维分形磁贴（实体面积>0），不同于Heighway Dragon的“线”。  
- **Twindragon Curve**：Twin Dragon对应的边界曲线（维数2），又称Davis–Knuth曲线。  
- **Twindragon Tile vs Curve**：双龙磁贴（2D瓷砖）和龙曲线（1D路径）的区别要清晰，后者不是基数分形。
- 更一般地，**高斯整数$\mathbb{Z}[i]$或艾森斯坦整数$\mathbb{Z}[\omega]$**中的基数系统（canonical number system）产生复杂磁贴，如**Hosoya曲线**等。Kenyon, Lagarias, Thurston等研究了磁贴可平铺条件（邻接图）。
- **Substitute Tiles**：一维的迭代也是可能的，如用替换规则生成符号数字串，其柱面坐标图形化后为磁贴。

这里要强调：不是所有通过地址生成的点集都有磁贴属性。要保证**可平铺性**，需满足一定的代数条件（如Pisot数条件）。很多磁贴设计与代数数论有关，例如利用$\beta$的最小多项式、剩余图来保证边界无重叠。

图导IFS可结合代数：每个图节点对应一个残类，边对应不同数字的映射。这等价于将代数进制视作图导IFS。区别在于图导IFS允许不同状态的吸引集结构不同，而单一IFS只描述一个集。

### V. 复动力系统 / 逃逸时间 (Complex Dynamics / Escape-Time Fractals)

典型研究双复变迭代$f_c(z)=z^2+c$及其推广。构造分形时，重点关注**复平面分割**：
- **Mandelbrot 集**：参数$c$平面中，令迭代$z_{n+1}=z_n^2+c$从$z_0=0$开始，若轨道不逃逸则$c$属于Mandelbrot集。其边界是分形曲线。  
- **Julia 集**：固定$c$，研究初值$z_0$平面。**Filled Julia**是所有不逃逸点集，Julia Set通常指边界。Julia边界和Mandelbrot集共享分形性质。  
- **多重曼德尔布罗**（$z^d+c$）一般化。  
- **Burning Ship**：迭代$z_{n+1}=(|Re(z_n)|+i|Im(z_n)|)^2+c$，产生类似Mandelbrot但形状不同的集合。  
- **Newton fractals**：对复数多项式$f(z)$施行牛顿迭代$z_{n+1}=z_n - f(z_n)/f'(z_n)$。图像将复平面染色为最终收敛到各根的吸引域，边界通常分形。  
- **其他有理迭代**：如双二次映射、拉姆雷映射等可产生分形集合（Fatou & Julia理论）。

**参数平面 vs 动态平面**：Mandelbrot在参数$c$平面，Julia在初值平面。逃逸时间算法常用于可视化：对每个像素点迭代一定步数，记录逃逸时刻（或未逃逸标记）用于着色。**注意**：渲染上有色彩平滑、双线性插值等技术，但**数学定义**是集合划分，不依赖颜色。要区分分形集本身与视觉输出的艺术效果（基于轨道周期、Lyapunov指数、多色着色等）。

本组分形属于**动力系统分形**，与前面讲究严格自相似的IFS不同，它们的生成来自非线性迭代而非仿射收缩。分形维度可通过Lyapunov方法估计（如Kaplan–Yorke公式），Hausdorff维数一般很难解析求出，但可通过盒计数法或测度理论近似。

### VI. 实一维动力系统与分岔 (1D Maps and Chaos)

一维迭代映射$x_{n+1}=f(x_n)$尽管简单，也会产生分形现象：
- **Logistic 映射**$f(x)=rx(1-x)$：随着参数$r$变化，从稳定固定点到周期-2倍增再到混沌，出现**Feigenbaum分岔**。在混沌区域，不同初值下的轨道构成Cantor集结构的混沌吸引子。  
- **Tent映射、分数布朗利**等：生成类似Cantor集的分形。  
- **典型分岔图**：以$r$为横轴，$x_n$图像纵轴得到的“吸引图”，虽非某个集合的极限（以便研究动力性），但显示参数的分形分布（有时也可看作参数空间上的不稳定集合）。  
- **象限映射（Hénon映射）**：2D映射但可视作高维动力下出现的分形吸引子，常产生**马蹄形（horseshoe）**行为，对偶的Cantor集结构。  
- 数学上可用**符号动力学**描述马蹄映射，通过二进制序列映射点的轨迹，间接构造Cantor型集。  

需要分清：1D映射的“分形”通常是轨道吸引子的拓扑性质，而不是几何图形（除标度行为）。1D轨道图多用盒维或Lyapunov指数分析。

### VII. 连续时间混沌与物理系统 (Continuous Chaotic Systems)

典型三维常微分方程系统产生的分形：
- **Lorenz 吸引子**：三方程生成蝴蝶型吸引子，其相轨道落在Hausdorff维约2.06的集上。
- **Rössler 吸引子**：另一个经典3D混沌吸引子。  
- **双摆（Double Pendulum）**：二摆系统有4维相空间，常考虑截面（如固定初始角速度）画图。双摆系统可通过Euler–Lagrange方程数值积分得到混沌相图。方法：选取二维初始角$(\theta_1,\theta_2)$网格，每点固定开始速度（或其它方案），积分到一定时间或能量不变后，着色表示末态特征（如最终角度/稳定周期/运动的最大角度等），即可看到“混沌海”与“稳定岛”。**物理意义**：Stable island对应周期解或准周期解的吸引域；chaotic sea对应敏感依赖的初始条件区域。  
  - 需要注意：双摆虽产生复杂图案，但严格分形定义难以直接应用（不同于IFS极限）。常用Lyapunov指数和频谱分析混沌度。  
  - **实现注意**：使用高阶积分器（RK4或更好），保持能量约束；角度需模$2\pi$处理；选取合理积分时长；可用二维色轮表示动量方向、周期、分离率等。  
- **Poincaré截面、Basins of Attraction**：很多实验图是绘制长时间内轨道交截面（如Lorenz平面切割）或初值吸引域（rainbow basin）。如不同初值流体粒子落点分布等。

以上通常要求解决常微分方程，不属于简单的自相似几何生成方法，但有相似的分形性质（敏感依赖导致相空间结构呈现分形边界）。重点在于**混沌动力学**，涉及Lyapunov指数、奇怪吸引子理论。推荐参考Devaney《混沌系统简介》或Kuznetsov等物理/数学教程。

### VIII. 随机分形 (Random Fractals / Processes)

通过随机过程获得分形结构的集合或曲线：
- **Brownian Motion（布朗运动）**：连续但处处不光滑，样本路径Hausdorff维为2（在平面或1D时间参数上），虽拓扑维1。  
- **Fractional Brownian Motion (fBm)**：一般Hurst指数$H$控制粗糙度，维数为$2-H$或相应的统计属性。  
- **DLA（扩散限制聚集）**：随机游动粒子在簇外附着生成无规树状结构，典型维数$\approx1.71$。  
- **分形降雨或分形河网模型**：模拟自然现象的随机模型。  
- **随机Cantor集**：在每级随机决定保留哪些子段，产生统计分形。  
- **树形过程**：随机生成的分岔树（Galton–Watson过程等）在嵌入空间中的轨迹可生成随机树分形。  
- **多重分形体系**：概率级数如流体湍流的能量耗散服从乘积随机过程（联级模型），结果是多重分形。常用**$f(\alpha)$谱**描述。

特点：样本间存在差异，不是单一定义的集合。多以**期望维数**或**典型维数**描述，往往通过**统计自相似性**或结构函数刻画。分析工具包括谱函数、游程统计、随机微分等。

### IX. 分形曲面与自然纹理 (Fractal Surfaces, Terrain, Natural Fractals)

主要是**统计分形**技术，用于模拟自然景观等：
- **Diamond-Square算法**：随机高度插值生成高低起伏的地形（分形噪声）。  
- **分形布朗ian地形/云彩**：用分数布朗运动模拟2D纹理，常用Perlin noise和k次插值。  
- **多尺度随机场**：Kolmogorov湍流理论下的速度场模拟，产生分形流线。  
- **生物分形**：树/叶/血管等结构多用分枝系统模拟。  
- **自然现象**：海岸线、雪花形状、岩石裂缝、火焰路径、闪电路径（Lévy flights）、毛刺边界等。  
- **注意**：这些往往**在统计意义上**自相似，在有限尺度内有效；并非精确数学分形，但以分形维度或多重分形分析其粗糙度和结构。维数可以用盒维估计，但因尺度上限和分形范围有限须谨慎。Frank Mandelbrot的“自然的分形几何”一书（《自然的分形几何》）有大量实例。

### X. 圆反演与Kleinian群 (Circle Inversion and Kleinian Groups)

利用几何群体获得分形图案：
- **圆反演**：给定曲线/圆，通过反演生成变形，如Kelvin反演。  
- **Apollonian Packing**：从三圆和界圆开始，不断填充间隙，生成无限圆堆，具有分形性质（圆隙处为2D Cantor集）。  
- **Descartes Circle Theorem**：给出三切圆确定第四个圆的关系，应用于递归生成。  
- **Kleinian群吸引子**：对Riemann球上的Mobius群轨道取极限集，例如Schottky群、埃欣斯坦群产生的Cantor集、阿波利尼乌斯羊群。  
- **Ford 圆**：数论中表示有理数的Ford circle pack，可看作1D间断分形集的一种。  
- 此类方法关键是**反演代换**，常与群论和几何相关，经典参考有级数文章和狄奥多拉书籍。

### XI. 其他典型分形 (Other Examples)

- **Weierstrass函数**：处处连续不可微的函数图像，维数>1（Hausdorff维度$2-H$ if函数参数为$H$）。  
- **Takagi（布兰克曼尼奇）曲线**：分段线性定义的连续不可微曲线，经典分形曲线例子。  
- **Devil’s Staircase (Cantor函数)**：引导参数化的黎曼函数，水平平台交替，密集不连续处；严格是奇异连续函数，图形自相似。  
- **多重分形谱**：例如湍流和金融时间序列的多重分形分析，用$f(\alpha)$来描述不同局部粗糙度的分布。  
- **蕾丝/群体分形**：如尘封群（Julia群体）等拓扑分形。  
- 分形**插值函数**：给定控制点构造分形曲线，如Barnsley插值。

## C. 用户列对象逐个映射

我们用表格形式列出用户提出的分形及其主要生成方法（多种方式视情补充），并给出维数等定量信息（如可用Moran公式计算）。

| 对象（Object）          | 主要生成方法  | 其他方法   | 分形类型              | Hausdorff维(2D)/顶维 | 2D/3D | 数学产物            |
|------------------------|-------------|----------|---------------------|----------------------|-------|-------------------|
| Cantor 集 (Cantor set) | 删减 (去中心) | 2映射 IFS、3进制地址 | 自相似无连通      | $\log2/\log3\approx0.631$ | 1D    | Cantor测度等      |
| 肥 Cantor 集          | 删减 (保留更多) | —        | 正测度 Cantor集       | 1 (Lebesgue测度>0)  | 1D    | —                 |
| 谢尔宾斯基三角 (Sierpinski triangle) | 删减 (挖中心) | 3映射IFS, Chaos Game, 箭头曲线填充 | 自相似连通点集 | $\log3/\log2\approx1.585$ | 2D(点集) | 吸引子、解析公式 |
| 谢尔宾斯基地毯 (Carpet)     | 删减 (挖中方块) | 8映射IFS | 自相似Cantor尘  | $\log8/\log3\approx1.893$      | 2D(点集) | —                 |
| Vicsek 集           | 删减 (3×3保5块) | 5映射IFS | 自相似Cantor尘  | $\log5/\log3\approx1.465$      | 2D(点集) | —                 |
| Menger 海绵          | 删减 (27分格保20) | 20映射IFS | 3D自相似Cantor尘 | $\log20/\log3\approx2.727$     | 3D    | —                 |
| Koch 雪花 (Koch snowflake) | 边替换    | L-system  | 连续曲线 (闭环)   | $\log4/\log3\approx1.2629$     | 2D(曲线) | —                 |
| Minkowski 岛 (Minkowski sausage) | 边替换    | —        | 连续曲线         | $\log7/\log4\approx1.404$      | 2D(曲线) | —                 |
| Lévy C 曲线            | 边替换    | L-system  | 连续曲线         | 2                             | 2D(曲线) | —                 |
| Heighway 龙 (Heighway dragon) | 边替换    | L-system, Paperfolding | 连续曲线    | 2                             | 2D(曲线) | —                 |
| 双龙 (Twin dragon)    | 复数IFS   | 实现: $z\to(1+i)^{-1}(z+d)$, $d\in\{0,1\}$ | 自仿射磁贴/曲线 | 2           | 2D(区/曲线) | 磁贴             |
| Gosper 曲线 (flowsnake)  | L-system | 边替换  | 连续曲线         | 2                             | 2D(曲线) | —                 |
| Hilbert 曲线          | L-system | 节点替换 | 填充曲线 (连通填充正方) | 2         | 2D(曲线) | —                 |
| Peano 曲线          | L-system | — | 填充曲线 | 2 | 2D | — |
| Sierpiński 箭头 (arrowhead)  | L-system | 边替换 | 连续曲线(遍历三角) | 2 | 2D | — |
| Moore 曲线          | L-system | — | 连续闭合曲线 | 2 | 2D | — |
| H-树 (H-tree)        | 节点替换 | — | 分支图     | 1                             | 2D    | —                 |
| T-方块 (T-square)     | 节点替换 | — | 分支集合   | $\log5/\log2\approx2.322$ (若所有边均在中点连接?) | 2D | — |
| 毕达哥拉斯树 (Pythagoras tree) 方块型 | 节点替换 | — | 分支图 | 2 (填充面积无穷大?) | 2D | — |
| 分形树 (Fractal Tree)   | L-system, 节点替换 | — | 连续分支曲线 | - | 2D/3D | —            |
| Barnsley 蕨 (Barnsley fern) | IFS 随机 | 树状L-system | 自相似点集   | 1.50左右（估）        | 2D    | —                 |
| 黄金比例“五重环垫”（用户例子） | Chaos Game with 5 vertices, ratio $1/\varphi^2$ | — | 自相似点集？ | (需分析) | 2D    | 待定（非标准名） |
| Apollonian 垫片     | 圆反演 | Descartes圆定理 | 圆贴面集   | ≈1.3057(边界) | 2D    | 圆堆结构         |
| 曼德尔布罗集 (Mandelbrot) | 逃逸迭代 | — | 复参数集   | ~2? (边界1.4?) | 2D | 动态参数集合     |
| Julia 集            | 逃逸迭代 | — | 复平面集合   | ~2（边界维>1） | 2D | Julia集         |
| Burning Ship        | 逃逸迭代 | — | 自定义公式 | ~1.87? | 2D | 动力学轨迹集合   |
| Newton 分形         | 牛顿迭代 | — | 根吸引域   | 2 | 2D | 多边形分形边界   |
| Logistic map bifurcation diagram | 参数图 | — | 分岔结构   | 1?（Cantor集形式） | 1D图形 | —         |
| Double Pendulum phase portrait | ODE模拟 | — | 相空间分布 | 无简析维数 | 2D图像 | 混沌海与稳定岛   |

以上维数多依摩根公式或经验估计（标有【】者有文献支持）。注意**黄金比例五重环垫**（用户例）不是常见标准名称。据描述：用五边形顶点$v$和$f_n$定义$p_{n+1}=v+(p_n-v)/\varphi^2$迭代生成，此为带概率的5映射IFS，不同于Sierpinski三角（使用比例1/2）。在线搜索仅见未辨识实例。假若比率$1/\varphi^2\approx0.382$且取五个顶点，则形成某种*五角分形*，可能是“五星型Cantor集”或“五边形磁贴”。未查到标准称呼，故以**5-fold golden gasket**暂称。其Hausdorff维需解$\sum_{i=1}^5(\varphi^{-2})^s=1$。

**Menger海绵**之两种描述等价：既可看作20个缩小立方体并集，也可看作从立方体减去7小块。极限集一样；布尔运算只是实现选择。**Vicsek**和**T-square**相似，但前者保留中心+四角、后者似4叉结构，迭代方式不同。**Barnsley蕨**4个仿射映射的概率分别为0.01、0.85、0.07、0.07（参见Barnsley原著）。Chaos Game的分布与吸引子闭包一致，但需迭代足够大。**Chaos Game**如果使用不同比例（如$\varphi^{-2}$）和顶点集，则吸引子非标准Sierpinski。需验证是否达到平面填充或断裂。

## D. 分形的定量分析框架

常用度量：
- **Hausdorff维数** $\dim_H$：定义为使得Hausdorff测度跳变的维度。  
- **盒维数（盒计数维）** $\dim_B$：网格覆盖点集后的极限指数。对自相似集往往等于Hausdorff维。  
- **相似维数**：通过Moran公式$\sum r_i^s=1$解得$s$（自相似IFS）。  
- **自仿射维数**：对各向异缩放的IFS，常用Falconer公式（需要更高分析）。  
- **拓扑维数**：连通性相关（分形曲线为1、Cantor尘为0等）。  
- **Lyapunov指数**：动力系统分形的复杂度度量，可由$L=\lim_{n\to\infty}\frac1n \ln|df^n|$计算。  
- **逃逸时间**：分形可视化中用于着色，代表轨道逃逸所需迭代次数。  
- **多重分形谱**：非单一维度的复杂图形，如$f(\alpha)$谱、Reyni维数谱等。  
- **Lacunarity**：用于描述稀疏度或“孔洞”的度量，尤其对纹理分形重要。  
- **有限尺度和别名效应**：数字绘制中受像素大小限制，须注意采样率（Nyquist）避免伪差。  

对于**自相似集**，满足开集条件时Hausdorff维可按Moran公式严格计算。对于**随机和动力**分形，通常以数值经验估计为主。**自仿射集**（例如二维压缩比例不等）维数分析更复杂，一般使用Falconer(1988)多分辨率等方法。**逃逸集**（Julia/Mandelbrot边界）的维数一般需数值方法，理论上是激荡的参数依赖结果。**双摆分形边界**等需统计方法或有限时间Lyapunov测度。

## E. 主要生成算法与伪代码概述

以下给出各类生成算法的核心步骤（伪代码示意，不给编程语言）：

- **递归删减算法（Cut-Away）**：  
  ```
  function CutAway(shape, rule, depth):
      if depth == 0: return {shape}
      subshapes = ApplySplit(shape, rule) // 将shape分割为若干子形状
      for each sub in subshapes:
          if rule.keep(sub):
              keep union CutAway(sub, rule, depth-1)
      return union of kept parts
  ```  
  例：Sierpinski三角rule：把三角形分4个小三角形（按比例1/2），删除中央一个。

- **边替换曲线算法（Edge Replacement / L-system）**：  
  ```
  axiom = initial string, angle = given
  rules = {symbol -> replacement string}
  for iter=1..n:
      newstr = ""
      for char in axiom:
          if char in rules: newstr += rules[char]
          else: newstr += char
      axiom = newstr
  Draw(axiom): cursor at origin, heading initial.
      for each symbol in axiom:
          if symbol == 'F': draw forward segment
          if symbol == '+': turn right by angle
          if symbol == '-': turn left by angle
          // optional: '['/'']' for stack push/pop
  ```
  例如Koch：axiom="F--F--F", rules: F->"F+F--F+F", angle=60°。

- **节点分枝算法（Node Replacement / Fractal Tree）**：  
  类似L-system但允许分叉。使用栈或递归：  
  ```
  function DrawTree(length, angle, depth):
      if depth==0: return
      draw segment of length
      save state
      turn left by angle
      DrawTree(length*scale, angle, depth-1)
      restore state
      turn right by angle
      DrawTree(length*scale2, angle, depth-1)
  ```
  这是简单的二叉分枝，可扩展为多叉或不同缩放。

- **确定性IFS迭代**：  
  ```
  function IFS_Deterministic(iter):
      A = initial shape (e.g. unit cube)
      for k=1..iter:
          newA = empty set
          for each map S_i:
              newA += S_i(A)  // 仿射变换并集
          A = newA
      return A
  ```
  用于生成像Cantor之类的吸引子近似。在渲染时实际取$k\to\infty$。

- **随机Chaos Game**：  
  ```
  point = arbitrary starting point
  for n=1..N:
      choose map S_i with probability p_i
      point = S_i(point)
      plot point (discard initial transient)
  ```
  收敛时plot的点集密布在吸引子上。

- **Graph-Directed IFS**：类似确定性IFS，但维护多个集合$A_i$对应不同状态，使用有向图边更新：
  ```
  A_i = initial guess for all i
  for k=1..iter:
     for each state i:
        A_i_new = union over edges e: i->j of S_e(A_j)
     A_i = A_i_new for all i
  ```

- **复进制地址生成**：  
  对于复数基$\beta$和数字集$\mathcal D$，点为$\sum d_k\beta^{-k}$。可直接通过迭代：  
  ```
  tile_set = initial seed (often a square containing tile)
  for each d in D:
      new_tile += (tile_set + d) / beta
  repeat some iterations to approximate tile boundary.
  ```
  理论上无穷迭代逼近最终自仿射磁贴。

- **逃逸时间算法**：  
  对于每个像素（代表复平面点$z_0$），迭代$z_{n+1}=f(z_n)$直到$|z_n|>R$或达最大迭代。用迭代次数$N$决定颜色，未逃逸者标为所属集合。  
  ```
  for each pixel c in view:
      z=0  // for Mandelbrot；或者 z=c for Julia
      for n=1..maxIter:
          z = z^2 + c  // 复动力方程
          if |z|>2: break
      colorPixel(c) based on n
  ```

- **Newton法分形**：类似，对每像素$z_0$应用牛顿迭代，判断收敛到哪个根，着色不同。  

- **Logistic映射网格图**：  
  ```
  for each parameter r in [rmin,rmax]:
     x = x0 (initial)
     iterate 许多次丢弃过渡值
     for next M times:
         x = r*x*(1-x)
         plot point (r,x)
  ```
  分岔图就是参数-状态的图。

- **双摆相图**：例如取初始角网格$(\theta_1,\theta_2)$，令角速为0，积分定时间$T$。  
  ```
  for each initial pair (θ1, θ2):
      solve ODE for time T (RK4 etc)
      record final angles or orbit特征
      color(θ1, θ2) accordingly
  ```

- **DLA (扩散聚集)**:  
  ```
  cluster = origin
  while size<目标:
     start walker at far处随机
     while True:
         walker 移动一步随机游走
         if walker adjacent to cluster:
             add walker to cluster
             break
         if walker out-of-bounds:
             break or reset walker
  ```

- **Diamond-Square算法**：生成地形高程：  
  ```
  initialize grid corners
  for step_size decreasing by /2:
      // Diamond
      for each square:
          center = avg(corners) + random_offset
      // Square
      for each diamond:
          midpoint = avg(neighbors) + random_offset
      reduce random_offset amplitude
  ```
  
详细伪代码可根据需要进一步扩充。算法复杂度多呈**指数增长**（节点、边数、样本量对数关系）。绘制时注意缓存优化，避免重复计算。

## F. 同一分形的多种构造 vs 外观相似的不同分形

- **等价构造**：如果不同方法生成的分形**极限集**（点集/曲线轨迹）完全相同，则它们是同一对象。例如，谢尔宾斯基三角既可视为删减点集，也可由L-system曲线、IFS、Chaos Game产生同一点集。。对应条件：映射收缩比、转角序列等满足同样的数学等价。  
- **区别和反例**：  
  - **Heighway龙 vs Twin Dragon**：很多资料混用“龙曲线”称呼。Heighway龙（纸折曲线）和Twin Dragon（磁贴）**轨迹不同**：前者边替换角为$90°$的序列，后者基于复基$1+i$。它们的Hausdorff维都为2，但几何形状不同；Twin Dragon是Heighway龙的两片拼接形式。两者不能算同一吸引子。  
  - **Sierpiński箭头曲线 vs Sierpiński三角**：前者是通过L-system连续曲线来描绘三角点集的遍历路径，它的极限是一个曲线闭合路径；而三角点集本身是Cantor集。这两者拓扑不同（曲线vs无连通结构），虽外观相关，但**不是同一数学对象**。  
  - **Menger海绵的删减与并集**：保留20个缩立方（并集）与从原立方体减去7个小立方（差集）得到同一自相似点集，极限一致。但若考虑几何造型，前者是20个分散小块；后者是一个大立方中孔洞分布。数学上等价，渲染实体网格时却需要布尔差集。  
  - **IFS比例变化**：Chaos Game如果使用不同分配（如$\varphi^{-2}$而非1/2），不会生成经典谢尔宾斯基三角。必须严格匹配映射收缩率，才能保证产生相同K。  
  - **Hilbert曲线 vs Peano曲线**：两者都是填充空间的曲线，但轨迹生成方式和迭代结构不同。  
  - **Peano曲线 vs二维Cantor尘**：Peano是连续填满正方曲线，Cantor尘是非连通点集，表面现象都为碎形，但本质不同。

概括：**同一分形的多构造**需满足完全相同的极限几何/拓扑内容及收敛方式；**外形相似但构造不同**的情况常见，需要仔细区分其拓扑、测度和生成规则。

## G. 模块化软件架构建议

为实现“每条规则(基因)对应一条有向曲线/边段”，建议分层设计：

- **数学模型层 (FractalSpec/MathematicalModel)**：用抽象对象描述分形的生成规则（如IFS系统、L-system文法、节点评价规则、仿射映射集、ODE系统等）。包括参数：收缩矩阵、偏移、基数、概率、维度解析公式等。  
- **重写系统 / 图结构 (RewriteGrammar/DirectedGraph)**：对L-systems和图导IFS，用符号或图节点及边表示替换关系。每个规则实例（基因）对应一条边替换片段或仿射映射。  
- **矩阵与地址 (AffineMap/Address)**：用于IFS、数制分形，维护仿射矩阵($A_i,b_i$)，和复数基数$\beta$以及数字集合$\mathcal D$。  
- **几何原语 (Geometric Primitives)**：点、线段、正方形、圆、多边形、三维多面体等。还可包含布尔组合和网格算法，用于实现删减（CSG，构造实体几何）。  
- **动力系统/ODE（DynamicalSystem/ODE）**：定义连续时间系统，包含微分方程和积分器（RK4等）、初值、停止条件，输出轨迹或相空间截面。  
- **随机/概率模块 (StochasticSampler)**：管理随机数生成器和采样策略，用于Chaos Game、随机IFS、DLA、噪声等。  
- **渲染层 (Renderer)**：可以分2D矢量（Turtle绘图、线段集合）、栅格（逃逸图、纹理）、3D网格（分形表面、体积）等。支持常见格式（SVG、PNG、3D OBJ等）。  
- **并行与缓存**：支持多线程或GPU（shader）加速逃逸计算、体素构造等；缓存重用计算结果（例如IFS高深度次迭代点集）。

关键约定：统一坐标系（可灵活定义），角度单位（弧度/度），随机种子可控。提供**递归深度**、误差容限控制（如网格分辨率、积分步长）、终止条件（如最大迭代次数或最小尺度）。输出可包含**拓扑/几何结构**（如点云、边图、面片）或**像素着色图**。

## H. 教材、视频与资源推荐

### 教材与论文

- Hutchinson, **“Fractals and self-similarity”** (Indiana Univ. Math. J. 1981)：经典IFS基础文献。证明IFS吸引子唯一性和维数公式。  
- Moran, **“Additive functions of intervals and Hausdorff measure”** (Proc. Camb. Phil. Soc., 1946)：最早给出自相似集维度公式。  
- Falconer, **《Fractal Geometry: Mathematical Foundations and Applications》**（2nd ed., 2003）：广泛权威综述自相似与自仿射分形，维数理论（包含Moran定理、自仿射情况等）。  
- Edgar, **《Measure, Topology, Fractal Geometry》**（1990）：深入几何测度论基础、分形集理论，包括Cantor集、分形测度和维数。  
- Mattila, **《Geometry of Sets and Measures in Euclidean Spaces: Fractals and Rectifiability》**（1995）：几何测度论经典著作，涵盖Hausdorff维、投影与剖面等高级内容。  
- Peitgen, Jürgens, Saupe, **《Chaos and Fractals》**（1992）：介绍分形和混沌基础，案例丰富，插图精美（含Julia, Newton, DLA等）。  
- Prusinkiewicz & Lindenmayer, **《The Algorithmic Beauty of Plants》**（1990）：系统讲解L-system和自然分形，适合学习L-system编程实现。  
- Barnsley, **《Fractals Everywhere》**（2nd ed. 1993）：IFS经典教材，书中有众多程序示例和分形案例，建模和数学并重。  
- Devaney, **《A First Course in Chaotic Dynamical Systems》**（1992）：基础混沌动力系统入门，讲解“淋漓吸引子”（Strange attractor）的维数和例子。  
- 冯·克劳森(Claudia Fassler), **《Numerical Methods for Chaos*》**（假想参考）：适合查ODE和分岔/混沌的数值方法。（真实参考：Alligood, Sauer, Yorke《Chaos: An Introduction to Dynamical Systems》）  

### 在线课程与视频

- **李永乐老师**（Bilibili）：“雪花周长超过地球？1.26维图形”，通俗介绍Koch雪花及维数。链接：[哔哩哔哩搜索](https://www.bilibili.com) *(可搜索视频题目)*。  
- **Mama Miao (妈咪说 MommyTalk)**：一系列“分形与混沌”视频，第7集讲解仿射变换与IFS。可搜索“MamaTalk 分形混沌”。  
- **The Coding Train (Dan Shiffman)**：编程演示L-system、分形树、Barnsley蕨、Koch曲线、IFS等。视频实操性强。推荐关键词“Coding Train L-systems” 或“YouTube: Coding Challenge Fractals”。  
- **3Blue1Brown (Grant Sanderson)**：可视化数学频道。推荐“Beyond the Mandelbrot set: intro to holomorphic dynamics”（讲解Mandelbrot/Julia集本质）。  
- **Numberphile**：例如“Chaos Game”与“Beautiful curves”视频。展示谢尔宾斯基三角生成和Turtle曲线绘制。  
- **VisualMath** / **Mathologer**：知乎或YouTube常有分形科普视频，例如Sierpinski地毯、Gasket等。  
- **MIT OCW / 公开课**：UCLA、普林斯顿等学校有关于分形和混沌的课程幻灯片录像。搜索“Fractal geometry lecture MIT”或“Dynamical Systems lecture UC Berkeley”。  

### 图书馆与阅读路线

建议先看入门科普或综述，再读专业教科书：

- 入门：Falconer的入门书、Barnsley的示例书；或Peitgen的科普性质书。
- 中级：Devaney的混沌教科书（对Julia/Mandelbrot有易懂讲解）、Prusinkiewicz的程序实现书。
- 高级：Falconer、Edgar、Mattila对理论深入；Kigami《Analysis on Fractals》、Milnor《Dynamics in One Complex Variable》研究更深领域。  
- 论文：Mauldin-Williams(1988)图导IFS，Lapidus分形管理论，Barnsley-Harrington(1985)Chaos Game等原著。

**工具书网站**：  
- Wolfram MathWorld（英语）上的各分形条目。  
- Fractal Foundation网站（英文）提供案例和动图。  

## 结束语

本报告系统梳理了分形几何的主要理论、对象和算法，并给出了相应数学框架和参考文献。内容极为丰富，实际实现时可根据应用需求选用合适模块：几何递归、替换系统、IFS、数值迭代或随机采样等。每个模块背后的数学理论（维数、存在性、收敛性）都需谨慎对待。文中引用了Hutchinson (1981) 的迭代函数系统定理、Falconer (2003) 关于自相似集维度等经典结果，确保严谨性。所提资源和书目为中外权威之作，读者可据此深入各个专题。总之，分形几何不局限于某个特殊对象或方法，而是多领域交叉的丰富学科，本文旨在提供一个全面的视角和可执行的构建蓝图。

**参考文献：**文中已标注的为引用源，例如 Hutchinson 1981、Falconer 2003等，可据此查阅原文。具体书目见正文并请参阅各大学课程或图书资源获取完整文献。