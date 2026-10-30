"""Show the finite search, project scores and the failed review threshold."""

import hashlib,json,shutil
from fractions import Fraction as F
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
COLORS=['#235d72','#b65f27','#667782']
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'svg.fonttype':'none','path.simplify':False,'axes.spines.top':False,'axes.spines.right':False,'text.color':'#233b49','axes.labelcolor':'#233b49','axes.edgecolor':'#a5b2b7','savefig.facecolor':'white'})


def read(name):return json.loads((ROOT/name).read_text(encoding='utf-8'))


def finish(fig,axes,name,items):
    for index,ax in enumerate(axes):ax.patch.set_gid('data-area-'+str(index))
    for item in items:item.pop('artist').set_gid(item['id'])
    files=[]
    for ext in ['pdf','svg','png']:
        path=ROOT/'figures'/(name+'.'+ext);fig.savefig(path,dpi=155 if ext=='png' else 100,metadata={'Date':None} if ext=='svg' else None)
        if ext=='svg':path.write_bytes(('\n'.join(line.rstrip() for line in path.read_text(encoding='utf-8').splitlines())+'\n').encode())
        files.append(path)
    shutil.copyfile(ROOT/'figures'/(name+'.pdf'),ROOT/'paper/images'/(name+'.pdf'))
    result=dict(figure=name,axes=[dict(xlim=list(ax.get_xlim()),ylim=list(ax.get_ylim())) for ax in axes],items=items,files={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
    plt.close(fig);return result


def main():
    (ROOT/'paper/images').mkdir(parents=True,exist_ok=True)
    maps=[];search=read('results/development/search.json');dev=read('results/development/summary.json');final=read('results/final/summary.json');name=final['selected_name'];pred=read('results/final/predictions.json')
    fig,ax=plt.subplots(figsize=(8.8,4.6));fig.subplots_adjust(left=.10,right=.97,bottom=.20,top=.73);items=[]
    for j,cut in enumerate([14,30,60]):
        for k,strength in enumerate([1,5,20]):
            row=next(r for r in search if r['cutpoint_days']==cut and r['prior_strength']==strength);value=row['metrics']['macro_brier']['value'];x=j+[-.23,0,.23][k]
            bar=ax.bar(x,value,width=.20,color=COLORS[k],label='Prior '+str(strength) if j==0 else None)[0]
            display=f'{value:.3f}';ax.text(x,value+.006,display,ha='center',fontsize=8)
            items.append(dict(artist=bar,id='search-'+str(cut)+'-'+str(strength),axis=0,kind='search',cutpoint=cut,strength=strength,x=x,width=.20,value=value,display=display))
    baseline=dev['baseline_validation']['macro_brier']['value'];line=ax.axhline(baseline,color='#6c3441',ls='--',lw=1.1,label='Pooled baseline')
    items.append(dict(artist=line,id='validation-baseline',axis=0,kind='baseline_line',value=baseline))
    ax.set(xticks=range(3),xticklabels=['14 days','30 days','60 days'],xlim=(-.6,2.6),ylim=(0,.40),ylabel='Validation macro Brier score (lower is better)',xlabel='Authored split of the median of three past release intervals');ax.legend(frameon=False,ncol=4,loc='upper center',bbox_to_anchor=(.5,1.12));ax.yaxis.grid(alpha=.15);ax.set_axisbelow(True)
    fig.suptitle('Retain the whole search, including its boundary winner',x=.10,ha='left',fontweight='bold');fig.text(.10,.84,'30 validation origins across four development projects; training fits are unchanged.',fontsize=9);fig.text(.10,.035,'Current GitHub release metadata; finite authored grid. No global optimum or maintenance-failure diagnosis.',fontsize=8)
    maps.append(finish(fig,[ax],'validation_search',items))
    fig,ax=plt.subplots(figsize=(9.8,4.9));fig.subplots_adjust(left=.34,right=.97,bottom=.16,top=.76);items=[];base=final['models']['baseline'];selected=final['models'][name]
    labels=[]
    for j in range(5):
        y=4-j
        labels.append((base['per_repository'][j]['repository']+'\n'+str(base['per_repository'][j]['n'])+' origins / '+str(base['per_repository'][j]['positives'])+' gaps') if j<4 else 'Equal-weight project mean\n39 origins / 10 gaps')
        for k,model in enumerate(['baseline',name]):
            score=final['models'][model];value=(score['per_repository'][j]['brier']['value'] if j<4 else score['macro_brier']['value']);center=y+(.14 if k==0 else -.14)
            bar=ax.barh(center,value,height=.25,color=COLORS[k],label=('Pooled baseline' if k==0 else 'Frozen cadence rule') if j==0 else None)[0]
            display=f'{value:.3f}';ax.text(value+.006,center,display,va='center',fontsize=9)
            items.append(dict(artist=bar,id='final-'+str(j)+'-'+str(k),axis=0,kind='final_brier',model=model,repository_index=j,x=0,y=center,height=.25,value=value,display=display))
    ax.set(yticks=list(range(4,-1,-1)),yticklabels=labels,xlim=(0,.60),ylim=(-.6,4.6),xlabel='Final Brier score (lower is better)');ax.legend(frameon=False,ncol=2,loc='upper center',bbox_to_anchor=(.5,1.15));ax.xaxis.grid(alpha=.15);ax.set_axisbelow(True)
    fig.suptitle('Probability scores improve on this small final cohort',x=.05,ha='left',fontweight='bold');fig.text(.05,.855,'Four other projects, 2024–2025 origins; current snapshot, not historical vintages.',fontsize=9);fig.text(.05,.035,'The 0.5 review rule still misses all ten observed gaps. Lower Brier is not measured operational benefit.',fontsize=8)
    maps.append(finish(fig,[ax],'project_scores',items))
    fig,axes=plt.subplots(1,2,figsize=(10,4.8));fig.subplots_adjust(left=.08,right=.98,bottom=.23,top=.76,wspace=.32);items=[]
    ax=axes[0]
    for k,model in enumerate(['baseline',name]):
        members=[r for r in pred if r['model']==model]
        for index in range(5):
            subset=[r for r in members if min(4,int(F(r['p_numerator'],r['p_denominator'])*5))==index]
            if not subset:continue
            x=float(sum(F(r['p_numerator'],r['p_denominator']) for r in subset)/len(subset));y=sum(r['label_no_release_90d'] for r in subset)/len(subset)
            artist=ax.plot([x],[y],marker='o' if k else 'D',linestyle='None',color=COLORS[k],label=('Baseline' if not k else 'Cadence') if index==0 else None)[0]
            items.append(dict(artist=artist,id='calibration-'+str(k)+'-'+str(index),axis=0,kind='calibration',model=model,bin=index,x=x,value=y,n=len(subset)))
    ax.plot([0,1],[0,1],color='#9aa7ac',ls=':',lw=1);ax.axvline(.5,color='#6c3441',ls='--',lw=1);ax.text(.52,.94,'Authored review\nthreshold',fontsize=8,va='top');ax.set(xlim=(0,1),ylim=(0,1),xlabel='Mean predicted gap probability',ylabel='Observed gap fraction',title='Fixed probability bins');ax.legend(frameon=False,fontsize=8,loc='lower right')
    ax=axes[1];keys=['tp','fn','fp','tn'];labels=['Flagged\ngap','Missed\ngap','False\nflag','Unflagged\nnon-gap']
    for i,key in enumerate(keys):
        value=selected[key];bar=ax.bar(i,value,width=.60,color=[COLORS[0],COLORS[1],'#76526b',COLORS[2]][i])[0];ax.text(i,value+.5,str(value),ha='center',fontsize=10)
        items.append(dict(artist=bar,id='review-'+key,axis=1,kind='confusion',key=key,x=i,width=.60,value=value,display=str(value)))
    ax.set(xticks=range(4),xticklabels=labels,xlim=(-.6,3.6),ylim=(0,34),ylabel='Final release origins (count)',title='Predeclared probability ≥ 0.5');ax.yaxis.grid(alpha=.15);ax.set_axisbelow(True)
    fig.suptitle('No observed gap reaches the authored review threshold',x=.08,ha='left',fontweight='bold');fig.text(.08,.855,'The frozen training probabilities are below 0.5 in both cadence strata.',fontsize=9);fig.text(.08,.04,'39 origins, ten gaps; dependent intervals and an authored project cohort. No population or production guarantee.',fontsize=8)
    maps.append(finish(fig,list(axes),'calibration_and_misses',items))
    (ROOT/'figures/value_map.json').write_bytes((json.dumps(maps,indent=2)+'\n').encode());print(json.dumps(dict(figures=3,formats=['pdf','svg','png'],plot_items=sum(len(m['items']) for m in maps))))


if __name__=='__main__':main()
