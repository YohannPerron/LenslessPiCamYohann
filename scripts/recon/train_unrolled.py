# #############################################################################
# train_unrolled.py
# =================
# Authors :
# Yohann PERRON [yohann.perron@gmail.com]
# #############################################################################

"""
Train unrolled version of reconstruction algorithm.

```
python scripts/recon/train_unrolled.py
```

"""

import hydra
from hydra.utils import get_original_cwd
import os
import numpy as np
import time
from lensless import UnrolledFISTA, UnrolledADMM
from lensless.utils.dataset import DiffuserCamTestDataset, SimulatedDataset
from lensless.recon.utils import create_process_network
from lensless.utils.image import rgb2gray
from lensless.recon.utils import Trainer
import torch
from torchvision import transforms, datasets


def simulate_dataset(config, psf):
    # load dataset
    transforms_list = [transforms.ToTensor()]
    data_path = os.path.join(get_original_cwd(), "data")
    if config.simulation.grayscale:
        transforms_list.append(transforms.Grayscale())
    transform = transforms.Compose(transforms_list)
    if config.files.dataset == "mnist":
        ds = datasets.MNIST(root=data_path, train=True, download=True, transform=transform)
    elif config.files.dataset == "fashion_mnist":
        ds = datasets.FashionMNIST(root=data_path, train=True, download=True, transform=transform)
    elif config.files.dataset == "cifar10":
        ds = datasets.CIFAR10(root=data_path, train=True, download=True, transform=transform)
    elif config.files.dataset == "CelebA":
        ds = datasets.CelebA(root=data_path, split="train", download=True, transform=transform)
    else:
        raise NotImplementedError(f"Dataset {config.files.dataset} not implemented.")

    # convert PSF
    if config.simulation.grayscale:
        psf = rgb2gray(psf)
    if not isinstance(psf, torch.Tensor):
        psf = transforms.ToTensor()(psf)

    n_files = config.files.n_files
    device_conv = config.torch_device

    # check if gpu is available
    if device_conv == "cuda" and torch.cuda.is_available():
        device_conv = "cuda"
    else:
        device_conv = "cpu"

    # create Pytorch dataset and dataloader
    if n_files is not None:
        ds = torch.utils.data.Subset(ds, np.arange(n_files))
    ds_prop = SimulatedDataset(
        dataset=ds, psf=psf, dataset_is_CHW=True, device_conv=device_conv, **config.simulation
    )
    return ds_prop


@hydra.main(version_base=None, config_path="../../configs", config_name="unrolled_recon")
def train_unrolled(
    config,
):
    if config.torch_device == "cuda" and torch.cuda.is_available():
        print("Using GPU for training.")
        device = "cuda"
    else:
        print("Using CPU for training.")
        device = "cpu"

    # torch.autograd.set_detect_anomaly(True)

    # benchmarking dataset:
    path = os.path.join(get_original_cwd(), "data")
    benchmark_dataset = DiffuserCamTestDataset(
        data_dir=path, downsample=config.simulation.downsample
    )

    psf = benchmark_dataset.psf.to(device)
    background = benchmark_dataset.background

    # convert psf from BGR to RGB
    if config.files.dataset in ["DiffuserCam"]:
        psf = psf[..., [2, 1, 0]]

    # if using a portrait dataset rotate the PSF

    disp = config.display.disp
    if disp < 0:
        disp = None

    save = config.save
    if save:
        save = os.getcwd()

    start_time = time.time()

    # Load pre process model
    pre_process, pre_process_name = create_process_network(
        config.reconstruction.pre_process.network,
        config.reconstruction.pre_process.depth,
        device=device,
    )
    # Load post process model
    post_process, post_process_name = create_process_network(
        config.reconstruction.post_process.network,
        config.reconstruction.post_process.depth,
        device=device,
    )
    # create reconstruction algorithm
    if config.reconstruction.method == "unrolled_fista":
        recon = UnrolledFISTA(
            psf,
            n_iter=config.reconstruction.unrolled_fista.n_iter,
            tk=config.reconstruction.unrolled_fista.tk,
            pad=True,
            learn_tk=config.reconstruction.unrolled_fista.learn_tk,
            pre_process=pre_process,
            post_process=post_process,
        ).to(device)
    elif config.reconstruction.method == "unrolled_admm":
        recon = UnrolledADMM(
            psf,
            n_iter=config.reconstruction.unrolled_admm.n_iter,
            mu1=config.reconstruction.unrolled_admm.mu1,
            mu2=config.reconstruction.unrolled_admm.mu2,
            mu3=config.reconstruction.unrolled_admm.mu3,
            tau=config.reconstruction.unrolled_admm.tau,
            pre_process=pre_process,
            post_process=post_process,
        ).to(device)
    else:
        raise ValueError(f"{config.reconstruction.method} is not a supported algorithm")

    # constructing algorithm name by appending pre and post process
    algorithm_name = config.reconstruction.method
    if config.reconstruction.pre_process.network is not None:
        algorithm_name = pre_process_name + "_" + algorithm_name
    if config.reconstruction.post_process.network is not None:
        algorithm_name += "_" + post_process_name

    # print number of parameters
    print(f"Training model with {sum(p.numel() for p in recon.parameters())} parameters")
    # transform from BGR to RGB
    transform_BRG2RGB = transforms.Lambda(lambda x: x[..., [2, 1, 0]])

    # load dataset and create dataloader
    if config.files.dataset == "DiffuserCam":
        # Use a ParallelDataset
        from lensless.utils.dataset import ParallelDataset

        max_indices = 30000
        if config.files.n_files is not None:
            max_indices = config.files.n_files + 1000

        data_path = os.path.join(get_original_cwd(), "data", "DiffuserCam")
        dataset = ParallelDataset(
            root_dir=data_path,
            indices=range(1000, max_indices),
            background=background,
            psf=psf,
            lensless_fn="diffuser_images",
            lensed_fn="ground_truth_lensed",
            downsample=config.simulation.downsample / 4,
            transform_lensless=transform_BRG2RGB,
            transform_lensed=transform_BRG2RGB,
        )
    else:
        # Use a simulated dataset
        dataset = simulate_dataset(config, psf)

    # test mask
    from lensless.recon.trainable_mask import AmplitudeMask

    mask = AmplitudeMask(torch.rand_like(psf), optimizer="Adam", lr=1e-3)

    print(f"Setup time : {time.time() - start_time} s")

    trainer = Trainer(
        recon,
        dataset,
        benchmark_dataset,
        mask=mask,
        batch_size=config.training.batch_size,
        loss=config.loss,
        lpips=config.lpips,
        optimizer=config.optimizer.type,
        optimizer_lr=config.optimizer.lr,
        slow_start=config.training.slow_start,
        skip_NAN=config.training.skip_NAN,
        algorithm_name=algorithm_name,
    )

    trainer.train(n_epoch=config.training.epoch, save_pt=save, disp=disp)
    trainer.save(path=os.path.join(save, "recon.pt"))
    if mask is not None:
        print("Saving mask")
        print(f"mask shape: {mask._mask.shape}")
        torch.save(mask._mask, os.path.join(save, "mask.pt"))
        # save as image using plt
        import matplotlib.pyplot as plt

        print(f"mask max: {mask._mask.max()}")
        print(f"mask min: {mask._mask.min()}")
        plt.imsave(os.path.join(save, "mask.png"), mask._mask.detach().cpu().numpy()[0, ...])


if __name__ == "__main__":
    train_unrolled()
