"""
Global statistical analysis of SPM maps produced by first-level analyis  of the dataset.
* tease out effect of subject, task and phase encoding direction
* Study global similarity effects  

Author: Bertrand Thirion, 2017
"""
import glob
import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import nibabel as nib
from nilearn.input_data import NiftiMasker
from joblib import Memory, Parallel, delayed
from nilearn import plotting
from nilearn.image import math_img
from sklearn.preprocessing import OneHotEncoder, LabelEncoder
from nistats.thresholding import map_threshold
from data_utils import data_parser

DERIVATIVES = '/neurospin/ibc/derivatives'
SMOOTH_DERIVATIVES = '/neurospin/ibc/smooth_derivatives'
SUBJECTS = [os.path.basename(full_path) for full_path in
            sorted(glob.glob(os.path.join(DERIVATIVES, 'sub-*')))]
CONDITIONS = pd.DataFrame().from_csv('../processing/conditions.tsv', sep='\t')
cache = '/neurospin/tmp/bthirion'
mem = Memory(cachedir=cache, verbose=0)


def design(feature):
    enc = LabelEncoder().fit(feature)
    feature_label, feature_ = enc.transform(feature), enc.classes_
    dmtx = OneHotEncoder(sparse=False).fit_transform(feature_label.reshape(-1, 1))
    return dmtx, feature_ 


def anova(db, masker):
    """perform a big ANOVA of brain activation with three factors:
    acquisition, subject, contrast"""
    df = db[(db.acquisition == 'ap') | (db.acquisition == 'pa')]

    # make the design matrix
    subject_dmtx, subject_ = design(df.subject)
    contrast_dmtx, contrast_ = design(df.contrast)
    acq_dmtx, acq_ = design(df.acquisition)
    dmtx = np.hstack((subject_dmtx[:, : -1],
                      contrast_dmtx[:, : -1],
                      acq_dmtx[:, : -1],
                      np.ones((len(df), 1)))) 
    labels = np.hstack((subject_[: -1], contrast_[: -1], acq_[: -1], ['intercept']))
    design_matrix = pd.DataFrame(dmtx, columns=labels)
    _, singular, _ = np.linalg.svd(design_matrix.values, 0)
    dof_subject = len(subject_) - 1
    dof_contrast = len(contrast_) - 1
    dof_acq = len(acq_) - 1
    
    # fit the model
    from nistats.second_level_model import SecondLevelModel
    second_level_model = SecondLevelModel(mask=masker.mask_img_)
    second_level_model = second_level_model.fit(list(df.path.values),
                                                design_matrix=design_matrix)
    subject_map = second_level_model.compute_contrast(
        np.eye(len(labels))[:dof_subject], output_type='z_score')
    contrast_map = second_level_model.compute_contrast(
        np.eye(len(labels))[dof_subject: dof_subject + dof_contrast],
        output_type='z_score')
    acq_map = second_level_model.compute_contrast(
        np.eye(len(labels))[-1 -dof_acq: -1], output_type='z_score')
    subject_map = math_img('img * (img > -8.2095)', img=subject_map)
    contrast_map = math_img('img * (img > -8.2095)', img=contrast_map)
    acq_map =  math_img('img * (img > -8.2095)', img=acq_map)
    return design_matrix, subject_map, contrast_map, acq_map


def global_similarity(db, masker):
    """Study the global similarity of ffx activation maps"""
    df = db[db.acquisition == 'ffx']
    X = masker.transform(df.path)
    xcorr = np.corrcoef(X)
    subject_dmtx, subject_ = design(df.subject)
    contrast_dmtx, contrast_ = design(df.contrast)
    scorr = np.dot(subject_dmtx, subject_dmtx.T)
    ccorr = np.dot(contrast_dmtx, contrast_dmtx.T)
    plt.figure(figsize=(7.2, 5))
    ax = plt.axes([0.01, 0.01, .58, .94])
    ax.imshow(xcorr, interpolation='nearest', cmap=plotting.cm.bwr)
    ax.axis('off')
    ax.set_title('Between image correlation', fontdict={'fontsize':14})
    ax = plt.axes([.61, 0.01, .38, .44])
    ax.imshow(scorr, interpolation='nearest', cmap=plotting.cm.bwr)
    ax.axis('off')
    ax.set_title('Correlation due to subject')
    ax = plt.axes([.61, 0.51, .38, .44])
    ax.imshow(ccorr, interpolation='nearest', cmap=plotting.cm.bwr)
    ax.axis('off')
    ax.set_title('Correlation due to contrast')
    plt.savefig(os.path.join('output', 'similarity.pdf'))

    from sklearn.manifold import TSNE
    model = TSNE(n_components=2, random_state=0)
    Y = model.fit_transform(X)
    plt.figure()
    color_code = plt.cm.jet(np.linspace(0, 255, 12).astype(np.int))
    colors = color_code[LabelEncoder().fit_transform(df.subject) - 1]
    plt.scatter(Y[:, 0], Y[:, 1], color=colors)
    plt.show()


def condition_similarity(db, masker):
    """ Look at the similarity across conditions, averaged across subjects and phase encoding"""
    df = db[db.acquisition == 'ffx']
    conditions = df.contrast.unique()
    n_conditions = len(conditions)
    correlation = np.zeros((n_conditions, n_conditions))
    X = {}
    unique_subjects = df.subject.unique()
    n_subjects = len(unique_subjects)
    for subject in unique_subjects:
        paths = []
        tasks = []
        for condition in conditions:
            selection = df[df.subject == subject][df.contrast == condition]
            tasks.append(selection.task.values[-1])
            paths.append(selection.path.values[-1])
        x = masker.transform(paths)
        correlation += np.corrcoef(x)
        X[subject] = x

    tasks = np.array(tasks) 
    unique_tasks = np.unique(tasks)
    task_pos = np.array(
        [np.mean(np.where(tasks == task)[0]) for task in unique_tasks])
    nice_tasks = np.array([task.replace('_', ' ') for task in unique_tasks])

    # plot with subject correlations
    plt.figure(figsize=(5, 5))
    ax = plt.axes()
    ax.set_yticks(task_pos)
    ax.set_yticklabels(nice_tasks)
    ax.set_xticks(task_pos)
    ax.set_xticklabels(nice_tasks, rotation=60, ha='right')
    ax.imshow(correlation, interpolation='nearest', cmap=plotting.cm.bwr)
    plt.subplots_adjust(left=.25, top=.99, right=.99, bottom=.2)
    plt.savefig(os.path.join('output', 'condition_similarity_within.pdf'))

    # plot cross-subject correlation
    correlation_ = np.zeros((n_conditions, n_conditions))
    for i in range(n_subjects):
        for j in range(i):
            X_ = np.vstack((X[unique_subjects[i]], X[unique_subjects[j]]))
            correlation_ += np.corrcoef(X_)[n_conditions:, :n_conditions]
            
    correlation_ /= (n_subjects * (n_subjects - 1) * .5)
    plt.figure(figsize=(5, 5))
    ax = plt.axes()
    ax.set_yticks(task_pos)
    ax.set_yticklabels(nice_tasks)
    ax.set_xticks(task_pos)
    ax.set_xticklabels(nice_tasks, rotation=60, ha='right')
    ax.imshow(correlation_, interpolation='nearest', cmap=plotting.cm.bwr)
    plt.subplots_adjust(left=.25, top=.99, right=.99, bottom=.2)
    plt.savefig(os.path.join('output', 'condition_similarity_across.pdf'))

    # similarity at the level of conditions
    cognitive_atlas = '../processing/cognitive_atlas.csv'
    df = pd.DataFrame().from_csv(cognitive_atlas, index_col=1, sep='\t')
    df = df.fillna(0)
    df = df.drop('Tasks', 1)
    cog_model = np.zeros((n_conditions, len(df.columns)))
    for i, condition in enumerate(conditions):
        cog_model[i] = df[df.index == condition].values
        print(condition, [df.columns[i] for i in range(50)
                          if df[df.index == condition].values[0][i]])

    ccorrelation = np.corrcoef(cog_model)
    plt.figure(figsize=(5, 5))
    ax = plt.axes()
    ax.set_yticks(task_pos)
    ax.set_yticklabels(nice_tasks)
    ax.set_xticks(task_pos)
    ax.set_xticklabels(nice_tasks, rotation=60, ha='right')
    ax.imshow(ccorrelation, interpolation='nearest', cmap=plotting.cm.bwr)
    plt.subplots_adjust(left=.25, top=.99, right=.99, bottom=.2)
    plt.savefig(os.path.join('output', 'condition_similarity_cognitive.pdf'))
    plt.show()
    x = np.triu(correlation, 1)
    y = np.triu(ccorrelation, 1)
    x = x[x != 0]
    y = y[y != 0]
    import scipy.stats as st
    print('pearson', st.pearsonr(x,y))
    print('spearman', st.spearmanr(x,y))
    

    
if __name__ == '__main__':
    db = data_parser(derivatives=SMOOTH_DERIVATIVES)
    mask_gm = nib.load(os.path.join(DERIVATIVES, 'group', 'anat', 'gm_mask.nii.gz'))
    masker = NiftiMasker(mask_img=mask_gm, memory=mem).fit()

    design_matrix, subject_map, contrast_map, acq_map = anova(db, masker)
    subject_map.to_filename(os.path.join('output', 'subject_effect.nii.gz'))
    contrast_map.to_filename(os.path.join('output', 'contrast_effect.nii.gz'))
    acq_map.to_filename(os.path.join('output', 'acq_effect.nii.gz'))
    # 
    _, threshold_ = map_threshold(subject_map, threshold=.05, height_control='fdr')
    plotting.plot_stat_map(subject_map, cut_coords=[10, -50, 10],
                           threshold=threshold_, title='Subject effect',
                           output_file=os.path.join('output', 'subject_effect.pdf'))
    #
    _, threshold_ = map_threshold(contrast_map, threshold=.05, height_control='fdr')
    plotting.plot_stat_map(contrast_map, cut_coords=[10, -50, 10],
                           threshold=threshold_, title='Condition effect',
                           output_file=os.path.join('output', 'contrast_effect.pdf'))
    #
    _, threshold_ = map_threshold(acq_map, threshold=.05, height_control='fdr')
    plotting.plot_stat_map(acq_map,
                           threshold=threshold_, title='Phase encoding effect',
                           output_file=os.path.join('output', 'acq_effect.pdf'))
    
    global_similarity(db, masker)
    condition_similarity(db, masker)
    