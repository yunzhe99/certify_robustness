"""
基于原来的certify改动，支持不平衡的情况，但是不支持平衡的情况了
"""

import os
import sys
sys.path.insert(0, './')

import pickle
import argparse
import numpy as np

import torch
import torch.nn as nn

from util.model_asys import MLP
from util.dataset import load_pkl, load_mnist, load_fmnist, load_svhn
from util.device_parser import config_visible_gpu
from util.param_parser import DictParser, ListParser, IntListParser

def var_init(batch_size, in_dim, init_value1, init_value2, device):
    '''
    >>> initialize the bounds \\epsilon
    '''
    var1 = torch.zeros([batch_size, in_dim], device = device, requires_grad = True)
    var2 = torch.zeros([batch_size, in_dim], device = device, requires_grad = True)
    var1.data.fill_(init_value1)
    var2.data.fill_(init_value2)
    var_list = [var1, var2]

    return var_list

def var_calc(batch_size, in_dim, var_list, device):


    var1, var2 = var_list
    eps1 = var1 * var1
    eps2 = var2 * var2

    return eps1, eps2

def clip_gradient(grad, length):
    '''
    >>> grad: tensor of shape [batch_size, in_dim]
    >>> length: the maximum length allowed
    '''
    grad_norm = torch.norm(grad, dim = 1).view(-1, 1) + 1e-8
    clipped_grad_norm = torch.clamp(grad_norm, max = length)

    return grad / grad_norm * clipped_grad_norm

if __name__ == '__main__':

    parser = argparse.ArgumentParser()

    parser.add_argument('--dataset', type = str, default = 'syn',
        help = 'specify the dataset to use, default = "syn"')
    parser.add_argument('--data', type = str, default = None,
        help = 'the data file to be loaded')

    parser.add_argument('--batch_size', type = int, default = 10,
        help = 'the batch size, default = 10')
    parser.add_argument('--batch_num', type = int, default = 20,
        help = 'the number of batches, default = 20')
    parser.add_argument('--subset', action = IntListParser, default = None,
        help = 'whether or not to load a subset of dataset, default = None')

    parser.add_argument('--model2load', type = str, default = None,
        help = 'the model to be loaded, default = None')

    parser.add_argument('--in_dim', type = int, default = 2,
        help = 'the number of input dimensions, default = 2')
    parser.add_argument('--hidden_dims', action = IntListParser, default = [],
        help = 'the number of neurons in hidden layers, default = []')
    parser.add_argument('--out_dim', type = int, default = 10,
        help = 'the number of classes, default = 10')
    parser.add_argument('--nonlinearity', type = str, default = 'relu',
        help = 'the activation function, default = "relu"')

    parser.add_argument('--max_iter', type = int, default = 400,
        help = 'the maximum iterations, default = 400')
    parser.add_argument('--beta', type = float, default = 1.,
        help = 'the coefficient of the augment term, default = 1.')
    parser.add_argument('--inc_rate', type = float, default = 5.,
        help = 'the ratio of increase in beta, default = 5')
    parser.add_argument('--inc_min', type = int, default = 0,
        help = 'the minimum iteration number for increasing the beta, default = 0')
    parser.add_argument('--inc_freq', type = int, default = 80,
        help = 'the frequency of the increase, default = 80')
    parser.add_argument('--update_dual_freq', type = int, default = 5,
        help = 'the frequency of updating dual variable, default = 5')

    # parser.add_argument('--mode', type = str, default = 'nonuniform',
    #     help = 'the type of the certified bound, default = "nonuniform", supported = ["nonuniform", "uniform","asymmetric"]')
    parser.add_argument('--init_margin1', type = float, default = 0.001,
        help = 'the bound initialization, default = 0.001')
    parser.add_argument('--init_margin2', type = float, default = 0.001,
        help = 'the bound initialization, default = 0.001')
    parser.add_argument('--norm', type = float, default = np.inf,
        help = 'the norm used for robustness, default = np.inf')
    parser.add_argument('--delta', type = float, default = 1e-4,
        help = 'the margin required to ensure the right prediction, default = 1e-4')
    parser.add_argument('--grad_clip', type = float, default = None,
        help = 'whether or not to apply gradient clipping, default = None')
    parser.add_argument('--final_decay', type = float, default = 0.99,
        help = 'the decay rate in the final search, default = 0.99')

    parser.add_argument('--optim', type = str, default = 'sgd',
        help = 'the type of the optimizer, default = "sgd"')
    parser.add_argument('--lr', type = float, default = 1e-3,
        help = 'the learning rate, default = 1e-3')

    parser.add_argument('--gpu', type = str, default = '0',
        help = 'choose which gpu to use, default = "0"')
    parser.add_argument('--out_file', type = str, default = None,
        help = 'the output file')

    args = parser.parse_args()
    config_visible_gpu(args.gpu)
    if not args.gpu in ['cpu',] and torch.cuda.is_available():
        device = torch.device('cuda:0')
        device_ids = 'cuda'
        use_gpu = True
    else:
        device = torch.device('cpu')
        device_ids = 'cpu'
        use_gpu = False

    if args.data is None and args.dataset.lower() in ['syn',]:
        raise ValueError('you should specify the input data')
    if args.out_file is None:
        raise ValueError('you should specify the output folder')
    out_dir = os.path.dirname(args.out_file)
    if out_dir != '' and os.path.exists(out_dir) == False:
        os.makedirs(out_dir)

    # Data Loader
    if args.dataset.lower() in ['syn',]:
        data_loader = load_pkl(pkl_file = args.data, batch_size = args.batch_size)
    elif args.dataset.lower() in ['mnist',]:
        data_loader = load_mnist(batch_size = args.batch_size, dset = 'test', subset = args.subset)
    elif args.dataset.lower() in ['fmnist',]:
        data_loader = load_fmnist(batch_size = args.batch_size, dset = 'test', subset = args.subset)
    elif args.dataset.lower() in ['svhn',]:
        data_loader = load_svhn(batch_size = args.batch_size, dset = 'test', subset = args.subset)
    else:
        raise ValueError('Unrecognized dataset: %s' % args.dataset.lower())

    # Model configuration
    # 加载先前训练好的一个模型
    model = MLP(in_dim = args.in_dim, hidden_dims = args.hidden_dims, out_dim = args.out_dim, nonlinearity = args.nonlinearity)
    model = model.cuda(device) if use_gpu else model
    ckpt = torch.load(args.model2load)
    model.load_state_dict(ckpt)
    model.eval()

    # Configure the certification parameter
    # 这边初始化一些参数
    init_value1 = np.sqrt(args.init_margin1) # 初始化的 bound，是一个标量，也平方了。
    init_value2 = np.sqrt(args.init_margin2)
    norm = np.inf if args.norm <= 0 else args.norm
    var_list = var_init(batch_size = args.batch_size, in_dim = args.in_dim, init_value1 = init_value1, init_value2 = init_value2, device = device)

    # Information to be saved
    tosave = {'config': {kwarg: value for kwarg, value in args._get_kwargs()}, 'results': []}

    # 对每个batch进行计算
    for batch_idx in range(args.batch_num):

        print('batch %d / %d' % (batch_idx, args.batch_num))

        data_batch, label_batch = next(data_loader) # data_loader 里面存放了batch的大小
        data_batch = data_batch.cuda(device) if use_gpu else data_batch # 优先加载到gpu上
        label_batch = label_batch.cuda(device) if use_gpu else label_batch
        data_batch = data_batch.view(data_batch.size(0), -1) # 按第一列的值标准化，防止出问题

        logits = model(data_batch) # 对 data_batch 进行预测，得到预测结果，这里是概率
        _, predict = torch.max(logits, dim = 1) # 得到预测结果
        result_mask = (predict == label_batch).float() # 这里用一个掩码来做预测了，得到了对应一个 batch 的结果
        label_mask = torch.ones([args.batch_size, args.out_dim], device = device).scatter_(dim = 1, index = label_batch.view(args.batch_size, 1), value = 0) # 也是返回一个关于 label 的 mask

        # Reinitialize the variable
        [p.data.fill_(init_value1) for p in var_list] # 这个用法还是第一次看到，不过大概能猜到，这个意思应该是将 var_list 用 init_value 填充
        beta = args.beta # 这个 beta 给个了初始化，未来是随着迭代不断更新
        grad_clip = args.grad_clip # 选择是否用这个方法，防止梯度爆炸
        lam = torch.zeros([args.batch_size, args.out_dim], device = device, requires_grad = False)

        # 选择优化器
        if args.optim.lower() in ['sgd',]:
            optim = torch.optim.SGD(var_list, lr = args.lr)
        elif args.optim.lower() in ['adam',]:
            optim = torch.optim.Adam(var_list, lr = args.lr)
        else:
            raise ValueError('Unrecognized Optimizer: %s' % args.optim.lower())

        # 开始训练
        for iter_idx in range(args.max_iter):

            # 首先根据变量来计算 eps
            eps1, eps2 = var_calc(batch_size = args.batch_size, in_dim = args.in_dim, var_list = var_list, device = device)

            low_bound, up_bound = model.bound(x = data_batch, ori_perturb_norm = norm, ori_perturb_eps1 = eps1, ori_perturb_eps2 = eps2)
            low_true = low_bound.gather(1, label_batch.view(-1, 1)) # 获取真实标签

            err = low_true - up_bound - args.delta
            err = torch.min(err, - lam / beta) * label_mask

            eps_loss = - torch.sum(torch.log(eps1) + torch.log(eps2), dim = 1)
            err_loss = torch.sum(lam * err, dim = 1) + beta / 2. * torch.norm(err, dim = 1) ** 2

            loss = torch.sum((eps_loss + err_loss) * result_mask) / torch.sum(result_mask)
            eps_v = torch.sum(eps_loss * result_mask) / torch.sum(result_mask)
            if iter_idx % 10 == 0:
                print(batch_idx, iter_idx, beta, eps_v.data.cpu().numpy(), (loss - eps_v).data.cpu().numpy())

            optim.zero_grad() #把梯度置0，重新算梯度
            loss.backward()
            # Gradient Clip
            if args.grad_clip is not None:
                for var in var_list:
                    var.grad.data = clip_gradient(var.grad.data, length = grad_clip)
            optim.step()

            if (iter_idx + 1) % args.update_dual_freq == 0:
                lam.data = lam.data + beta * err

            if iter_idx + 1 > args.inc_min and (iter_idx + 1 - args.inc_min) % args.inc_freq == 0:
                beta *= args.inc_rate
                if args.grad_clip is not None:
                    grad_clip /= np.sqrt(args.inc_rate)

        # Small adjustment in the end
        eps1,eps2 = var_calc(batch_size = args.batch_size, in_dim = args.in_dim, var_list = var_list, device = device)
        shrink_times = 0
        while shrink_times < 1000:

            low_bound, up_bound = model.bound(x = data_batch, ori_perturb_norm = norm, ori_perturb_eps1 = eps1, ori_perturb_eps2 = eps2)
            low_true = low_bound.gather(1, label_batch.view(-1, 1))
            err = low_true - up_bound - args.delta

            err_min, _ = torch.min(err * label_mask + 1e-10, dim = 1, keepdim = True)
            err_min = err_min * result_mask.view(-1, 1) + 1e-10

            if float(torch.min(err_min).data.cpu().numpy()) > 0:
                break

            shrink_times += 1
            err_sign = torch.sign(err_min)
            coeff = (1. - args.final_decay) / 2. * err_sign + (1. + args.final_decay) / 2.
            eps1.data = eps1.data * coeff
            eps2.data = eps2.data * coeff

        print('Shrink time = %d' % shrink_times)

        tosave['results'].append({'data_batch': data_batch.data.cpu().numpy(), 'predict': predict.data.cpu().numpy(), 'label_batch': label_batch.data.cpu().numpy(), 'result_mask': result_mask.data.cpu().numpy(), 'eps1': eps1.data.cpu().numpy(), 'eps2': eps2.data.cpu().numpy()})

        if (batch_idx + 1) % 10 == 0:
            pickle.dump(tosave, open(args.out_file, 'wb'))

    pickle.dump(tosave, open(args.out_file, 'wb'))
